from fastapi import FastAPI
from fastapi.responses import Response
from datetime import datetime
from typing import List, Dict
import requests
import os
import csv
import io
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json 

# Load environment variables
import time

app = FastAPI()
load_dotenv()

# ---- CONFIG ----
API_BASE_URL = "https://api.greeninvoice.co.il/api/v1"
API_ID = os.getenv("GREENINVOICE_API_ID", "your_api_id")
API_SECRET = os.getenv("GREENINVOICE_API_SECRET", "your_api_secret")

# ---- STEP 1: Get Bearer Token ----
def get_bearer_token() -> str:
    url = f"{API_BASE_URL}/account/token"
    payload = {
        "id": API_ID,
        "secret": API_SECRET
    }
    headers = {"Content-Type": "application/json"}
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()["token"]

# ---- STEP 2: Pull All Open Invoices ----
def get_open_invoices(token: str) -> List[Dict]:
    url = f"{API_BASE_URL}/documents/search"
    payload = {
        "status": 0
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    time.sleep(1)
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    json_data = response.json()

    # Extract from 'items' key if present
    if "items" in json_data:
        return json_data["items"]
    elif isinstance(json_data, list):
        return json_data
    else:
        return []
    
def upload_to_google_sheet(csv_data: str, sheet_name: str, worksheet_name: str):
    creds_dict = json.loads(os.getenv("GOOGLE_CREDS_JSON"))
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # Open and clear the sheet
    sheet = client.open(sheet_name)
    worksheet = sheet.worksheet(worksheet_name)
    worksheet.clear()

    # Convert CSV string to rows and upload
    rows = list(csv.reader(io.StringIO(csv_data)))
    worksheet.update(rows)


# ---- STEP 3: Group Invoices by Client ----
@app.get("/grouped-invoices")
def get_grouped_invoices():
    token = get_bearer_token()
    invoices = get_open_invoices(token)

    now = datetime.now()
    clients = {}

    for inv in invoices:
        client = inv.get("client", {})
        client_id = client.get("id", "unknown")
        client_name = client.get("name", "Unknown")
        client_email = ", ".join(client.get("emails", []))
        client_phone = client.get("phone", "")

        due_date_str = inv.get("dueDate") or inv.get("items", [{}])[0].get("dueDate")
        if not due_date_str:
            continue

        try:
            due_date = datetime.strptime(due_date_str[:10], "%Y-%m-%d")
        except ValueError:
            continue

        status = "past_due" if due_date < now else "to_be_paid"

        invoice_info = {
            "status": status,
            "id": inv.get("id"),
            "amount": inv.get("amount"),
            "description": inv.get("description"),
            "download_link": inv.get("url", {}).get("he"),
            "create_date": inv.get("documentDate"),
            "due_date": due_date_str
        }

        if client_id not in clients:
            clients[client_id] = {
                "client_name": client_name,
                "client_email": client_email,
                "client_phone": client_phone,
                "invoices": []
            }

        clients[client_id]["invoices"].append(invoice_info)

    # ---- CSV Construction ----
    # Determine max number of invoices any client has
    max_invoices = max(len(c["invoices"]) for c in clients.values()) if clients else 0

    # Build CSV headers
    base_headers = ["client_id", "client_name", "client_email", "client_phone"]
    invoice_headers = []
    for i in range(1, max_invoices + 1):
        invoice_headers.extend([
            f"invoice{i}_status",
            f"invoice{i}_id",
            f"invoice{i}_amount",
            f"invoice{i}_description",
            f"invoice{i}_download_link",
            f"invoice{i}_create_date",
            f"invoice{i}_due_date"
        ])
    headers = base_headers + invoice_headers

    # Write rows
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)

    for client_id, data in clients.items():
        row = [
            client_id,
            data["client_name"],
            data["client_email"],
            data["client_phone"]
        ]
        for invoice in data["invoices"]:
            row.extend([
                invoice["status"],
                invoice["id"],
                invoice["amount"],
                invoice["description"],
                invoice["download_link"],
                invoice["create_date"],
                invoice["due_date"]
            ])
        # Pad row to match header length
        row += [""] * (len(headers) - len(row))
        writer.writerow(row)
        
    csv_string = output.getvalue()
    print(csv_string)
    upload_to_google_sheet(csv_string, "Invoice Tracker", "Sheet1")

    return Response(content=csv_string, media_type="text/csv")
