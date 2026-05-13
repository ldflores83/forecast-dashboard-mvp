import os
from pathlib import Path
from dotenv import load_dotenv
from google.cloud import bigquery

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / '.env')
_creds = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', '')
if _creds and not Path(_creds).is_absolute():
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(_ROOT / _creds)

client = bigquery.Client(project="forecast-dashboard-mvp")
dataset_id = "forecast-dashboard-mvp.forecast_data"
dataset = bigquery.Dataset(dataset_id)
dataset.location = "us-central1"
client = bigquery.Client(project="forecast-dashboard-mvp")
dataset = client.create_dataset(dataset, exists_ok=True)
print(f"Dataset creado: {dataset.dataset_id}")