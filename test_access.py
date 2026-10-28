import requests
url = "https://medex.com.bd/brands/32979/tesco-dl-14-mg-syrup"
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
for i in range(5):
    r = requests.get(url, headers=headers, timeout=15)
    print(f"Request {i+1}: {r.status_code} - {'OK' if r.status_code == 200 else 'BLOCKED'}")
    if r.status_code == 200:
        print("SUCCESS! Ban is lifted!")
        break
    import time; time.sleep(1)
