from fastapi import FastAPI, Request, Query
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from aiocache import Cache
import httpx
import json
import asyncio
import base64
import gzip
import io


app = FastAPI()

# Allow CORS for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=".")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup caching
cache = Cache.from_url("redis://localhost:6379")  # Change to in-memory cache if Redis is unavailable


# Helper function to format time since posting
def time_ago(timestamp):
    now = datetime.utcnow()
    created_time = datetime.utcfromtimestamp(timestamp / 1000)
    delta = now - created_time

    if delta < timedelta(minutes=1):
        return "just now"
    elif delta < timedelta(hours=1):
        minutes = delta.seconds // 60
        return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    elif delta < timedelta(days=1):
        hours = delta.seconds // 3600
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    elif delta < timedelta(days=7):
        days = delta.days
        return f"{days} day{'s' if days > 1 else ''} ago"
    else:
        weeks = delta.days // 7
        return f"{weeks} week{'s' if weeks > 1 else ''} ago"

@app.get("/skyblock_item_list.json")
async def get_item_list():
    with open('skyblock_item_list.json', 'r') as file:
        items = json.load(file)
    return items

# Load items and map images
async def load_items():
    try:
        with open('skyblock_item_ids.json', 'r') as file:
            items = json.load(file)
        return items
    except Exception as e:
        print(f"Error loading items: {e}")
        return []

# Helper function to normalize strings
def normalize_string(s):
    """Normalize the string by converting to lowercase and removing special characters."""
    return ''.join(e for e in s if e.isalnum()).lower()

# Get auction data from API (Asynchronous version)
async def get_auctions(page=0):
    url = f'https://api.hypixel.net/v2/skyblock/auctions?page={page}'
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
    return response.json()

# Get seller name from UUID
async def get_username(uuid):
    url = f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
    if response.status_code == 200:
        return response.json().get('name', 'Unknown Seller')
    return 'Unknown Seller'

# Helper function to decode and decompress base64 item bytes
def decode_and_decompress(item_bytes_base64):
    try:
        # Decode from Base64
        item_bytes = base64.b64decode(item_bytes_base64)

        # Decompress the data using gzip
        with gzip.GzipFile(fileobj=io.BytesIO(item_bytes), mode='rb') as f:
            decompressed_data = f.read()

        # Return the raw binary data as a byte string in Python format (e.g., b'\n\x00\x00\t...')
        return {"type": "raw", "data": repr(decompressed_data)}  # repr() formats it like a byte string (e.g., b'\n\x00\x00\t...')

    except Exception as e:
        return {"type": "error", "data": str(e)}  # Return error message if any error occurs

# Load items asynchronously
async def load_items():
    try:
        with open('skyblock_item_ids.json', 'r') as file:
            items = json.load(file)
        return items
    except Exception as e:
        print(f"Error loading items: {e}")
        return []

# Update the search_auctions function to call load_items asynchronously
async def search_auctions(search_item):
    found_auctions = []
    items = await load_items()  # Ensure async loading of items

    normalized_search_item = normalize_string(search_item)

    for page in range(2):
        auctions = await get_auctions(page)
        if not auctions.get('success', False):
            continue
        
        for auction in auctions['auctions']:
            normalized_item_name = normalize_string(auction['item_name'])
            
            if normalized_search_item in normalized_item_name and auction.get('bin', False):
                image_url = next((item['png'] for item in items if normalize_string(item['name']) == normalized_item_name), None)

                auctioneer_uuid = auction.get('auctioneer', None)
                seller_name = await get_username(auctioneer_uuid) if auctioneer_uuid else 'Unknown Seller'
                
                auction_time = time_ago(auction['start'])

                item_bytes_base64 = auction.get('item_bytes')
                decoded_item_data = None
                count_section = None
                count_int = None
                if item_bytes_base64:
                    decoded_item_data = decode_and_decompress(item_bytes_base64)
                    if decoded_item_data['type'] == 'raw':
                        raw_data = decoded_item_data['data']
                        marker = "Count\\x"
                        start_index = raw_data.find(marker)
                        if start_index != -1:
                            start_index += len(marker)
                            following_data = raw_data[start_index:]
                            if len(following_data) >= 2:
                                count_value = following_data[0] + following_data[1]
                                count_int = int(count_value, 16)

                found_auctions.append({
                    "item_name": auction["item_name"],
                    "starting_bid": auction["starting_bid"],
                    "seller": seller_name,
                    "time_display": auction_time,
                    "image_url": image_url,
                    "count_section": count_int
                })

        await asyncio.sleep(1)  # Keep the rate-limiting intact

    return found_auctions


# Serve the main page
@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "results": []})

# API route for searching auctions
@app.get("/search")
async def search(search_item: str = Query(...)):
    # Check cache first
    cached_results = await cache.get(search_item)
    if cached_results:
        return {"results": json.loads(cached_results)}  # Return cached results if available

    # If not cached, search auctions
    auctions = await search_auctions(search_item)

    # Cache the results for 60 seconds
    await cache.set(search_item, json.dumps(auctions), ttl=60)

    return {"results": auctions}
