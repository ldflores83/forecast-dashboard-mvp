from google.cloud import bigquery
import os

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"C:\Users\luisd\Documents\Python projects\forecast-dashboard-mvp\credentials\forecast-dashboard-mvp-724e09b0b17a.json"

client = bigquery.Client(project="forecast-dashboard-mvp")
dataset_id = "forecast-dashboard-mvp.forecast_data"
dataset = bigquery.Dataset(dataset_id)
dataset.location = "us-central1"
client = bigquery.Client(project="forecast-dashboard-mvp")
dataset = client.create_dataset(dataset, exists_ok=True)
print(f"Dataset creado: {dataset.dataset_id}")