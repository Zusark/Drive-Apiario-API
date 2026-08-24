"""
Sincroniza los datos de las estaciones meteorológicas (Apiario Meteo)
desde Firebase Realtime Database hacia archivos CSV en Google Drive.

Estructura de datos en Firebase:
  /{ESTACION}/{DISPOSITIVO}/{FECHA "YYYY-MM-DD"}/{HORA "HH-MM-SS"}/{lecturas}

Genera un CSV por estación y lo sube (sobrescribiendo) a una carpeta de
Google Drive, usando una cuenta de servicio de Google Cloud.
"""

import csv
import io
import os
import sys

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ------------------------------------------------------------------
# Configuración
# ------------------------------------------------------------------

DB_BASE_URL = "https://estacion-apicultura-default-rtdb.firebaseio.com"

# Orden de columnas del CSV final
FIELDS = ["T1", "T2", "T3", "H1", "H2", "H3", "T4", "H4", "W1", "R1",
          "VBAT", "RSSI", "BOOT", "TIME_OK"]

CSV_HEADER = ["estacion", "dispositivo", "fecha", "hora", "timestamp"] + FIELDS

# Variables de entorno esperadas (se configuran como Secrets en GitHub Actions)
DRIVE_FOLDER_ID = os.environ["DRIVE_FOLDER_ID"]
SERVICE_ACCOUNT_FILE = os.environ.get("SERVICE_ACCOUNT_FILE", "service_account.json")

SCOPES = ["https://www.googleapis.com/auth/drive"]


# ------------------------------------------------------------------
# Lectura desde Firebase (REST API pública, sin autenticación)
# ------------------------------------------------------------------

def fetch_json(path, shallow=False):
    """Hace un GET a la Realtime Database. `path` sin barra inicial."""
    url = f"{DB_BASE_URL}/{path}.json"
    params = {"shallow": "true"} if shallow else {}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_stations():
    data = fetch_json("", shallow=True) or {}
    return sorted(data.keys())


def get_devices(station):
    data = fetch_json(station, shallow=True) or {}
    return sorted(data.keys())


def get_dates(station, device):
    data = fetch_json(f"{station}/{device}", shallow=True) or {}
    return sorted(data.keys())


def get_day_readings(station, device, date):
    data = fetch_json(f"{station}/{device}/{date}") or {}
    return data  # dict: { "HH-MM-SS": {campos...} }


# ------------------------------------------------------------------
# Armado del CSV
# ------------------------------------------------------------------

def build_station_csv(station):
    """Devuelve el contenido CSV (string) con todo el histórico de una estación."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)

    devices = get_devices(station)
    for device in devices:
        dates = get_dates(station, device)
        for date in dates:
            readings = get_day_readings(station, device, date)
            for hora, valores in sorted(readings.items()):
                hora_legible = hora.replace("-", ":")
                timestamp = f"{date} {hora_legible}"
                row = [station, device, date, hora_legible, timestamp]
                row += [valores.get(campo, "") for campo in FIELDS]
                writer.writerow(row)

    return buffer.getvalue()


# ------------------------------------------------------------------
# Subida a Google Drive
# ------------------------------------------------------------------

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def upload_or_replace_csv(drive_service, filename, content):
    """Si el archivo ya existe en la carpeta, lo sobrescribe. Si no, lo crea."""
    query = (
        f"name = '{filename}' and '{DRIVE_FOLDER_ID}' in parents "
        f"and trashed = false"
    )
    results = drive_service.files().list(
        q=query, fields="files(id, name)", spaces="drive"
    ).execute()
    files = results.get("files", [])

    media = MediaIoBaseUpload(
        io.BytesIO(content.encode("utf-8")), mimetype="text/csv", resumable=True
    )

    if files:
        file_id = files[0]["id"]
        drive_service.files().update(fileId=file_id, media_body=media).execute()
        print(f"  Actualizado: {filename} (id={file_id})")
    else:
        metadata = {"name": filename, "parents": [DRIVE_FOLDER_ID]}
        created = drive_service.files().create(
            body=metadata, media_body=media, fields="id"
        ).execute()
        print(f"  Creado: {filename} (id={created['id']})")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    print("Buscando estaciones activas...")
    stations = get_stations()
    print(f"  Encontradas: {stations}")

    drive_service = get_drive_service()

    for station in stations:
        print(f"Procesando estación: {station}")
        csv_content = build_station_csv(station)
        n_rows = csv_content.count("\n") - 1
        print(f"  {n_rows} lecturas")

        filename = f"{station}.csv"
        upload_or_replace_csv(drive_service, filename, csv_content)

    print("Listo.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
