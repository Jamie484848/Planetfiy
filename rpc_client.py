import time
import requests
from pypresence import Presence

CLIENT_ID = "1449141822407315662"
SERVER_URL = "https://planetfiy.onrender.com/"

rpc = Presence(CLIENT_ID)
rpc.connect()

print("✅ RPC verbunden")

while True:
    try:
        data = requests.get(f"{SERVER_URL}/rpc_state").json()

        if data["playing"]:
            rpc.update(
                details=data["title"],
                state=f"von {data['artist']}",
                start=data["start"],
                end=data["end"],
                large_image="planetify_logo",
                large_text="Planetify"
            )
        else:
            rpc.clear()

    except Exception as e:
        print("RPC Fehler:", e)

    time.sleep(5)