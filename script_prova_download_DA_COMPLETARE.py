from tqdm import tqdm
import requests
import json
import os

# =============================
# LETTURA CREDENZIALI
# =============================

username = os.getenv("CDSE_USER")
password = os.getenv("CDSE_PASS")
totp = input("Inserisci codice 2FA: ")

if username is None or password is None:
    print("Variabili ambiente mancanti")
    exit()

# =============================
# OTTENIMENTO TOKEN
# =============================

auth_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

data = {
    "client_id": "cdse-public",
    "username": username,
    "password": password,
    "totp": totp,
    "grant_type": "password"
}

response = requests.post(auth_url, data=data)

if response.status_code != 200:
    print("Errore autenticazione")
    print(response.text)
    exit()

access_token = response.json()["access_token"]

headers = {
    "Authorization": f"Bearer {access_token}"
}

print("Token ottenuto")

# =============================
# LETTURA GEOJSON
# =============================

with open("area_studio.geojson") as f:
    geo = json.load(f)

coords = geo["features"][0]["geometry"]["coordinates"][0]

if coords[0] != coords[-1]:
    coords.append(coords[0])

polygon_string = ",".join(f"{lon} {lat}" for lon, lat in coords)
odata_polygon = f"POLYGON(({polygon_string}))"

# =============================
# QUERY
# =============================

base_url = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"

query = (
    "?$filter=Collection/Name eq 'SENTINEL-1' "
    "and Attributes/OData.CSC.StringAttribute/any(a:"
    "a/Name eq 'productType' and a/Value eq 'GRD') "
    "and ContentDate/Start gt 2024-01-01T00:00:00.000Z "
    "and ContentDate/Start lt 2024-02-01T00:00:00.000Z "
    f"and OData.CSC.Intersects(area=geography'SRID=4326;{odata_polygon}')"
    "&$expand=Attributes"
)

url = base_url + query

print("Ricerca prodotti...")

# =============================
# RICERCA + PAGINAZIONE
# =============================

products = []

while url:

    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        print("Errore query")
        print(response.text)
        break

    data = response.json()

    products.extend(data["value"])

    url = data.get("@odata.nextLink")

print(f"Trovati {len(products)} prodotti")

# =============================
# CARTELLA DOWNLOAD
# =============================

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# =============================
# DOWNLOAD
# =============================

from tqdm import tqdm

for p in products:

    product_id = p["Id"]
    name = p["Name"]

    download_url = f"https://download.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value"

    print("\nScarico:", name)

    response = requests.get(
        download_url,
        headers=headers,
        stream=True,
        allow_redirects=True
    )

    if response.status_code != 200:
        print("Errore download:", response.status_code)
        continue

    total_size = int(response.headers.get("content-length", 0))

    file_path = os.path.join(DOWNLOAD_FOLDER, name + ".zip")

    with open(file_path, "wb") as f, tqdm(
        desc=name,
        total=total_size,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
    ) as bar:

        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                bar.update(len(chunk))

    print("✔ Download completato")
