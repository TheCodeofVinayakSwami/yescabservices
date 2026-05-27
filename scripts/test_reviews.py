from app import app
import json
c = app.test_client()
url = '/api/google_reviews?share_url=' + 'https://share.google/FDSFPaaaPgNjs1GxQ'
r = c.get(url)
print(r.status_code)
print(json.dumps(r.get_json(), indent=2))
