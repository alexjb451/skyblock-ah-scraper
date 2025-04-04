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

# Setup caching (adjust as necessary)
cache = Cache.from_url("redis://red-cv91uu23esus73b62s30:6379")  # Change to in-memory cache if needed

# Helper function to format the time since an auction was posted.
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

# Asynchronously load item data from JSON (for matching images, etc.).
async def load_items():
    try:
        with open('skyblock_item_ids.json', 'r') as file:
            items = json.load(file)
        return items
    except Exception as e:
        print(f"Error loading items: {e}")
        return []

# Helper function to normalize strings (removing non-alphanumeric characters and lowercasing).
def normalize_string(s):
    return ''.join(e for e in s if e.isalnum()).lower()

# Asynchronous function to get auction data from the Hypixel API.
async def get_auctions(page=0):
    url = f'https://api.hypixel.net/v2/skyblock/auctions?page={page}'
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
    return response.json()

# Asynchronous function to get the seller's username from their UUID.
async def get_username(uuid):
    url = f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
    if response.status_code == 200:
        return response.json().get('name', 'Unknown Seller')
    return 'Unknown Seller'

# Helper function to decode and decompress base64-encoded item bytes.
def decode_and_decompress(item_bytes_base64):
    try:
        item_bytes = base64.b64decode(item_bytes_base64)
        with gzip.GzipFile(fileobj=io.BytesIO(item_bytes), mode='rb') as f:
            decompressed_data = f.read()
        return {"type": "raw", "data": repr(decompressed_data)}
    except Exception as e:
        return {"type": "error", "data": str(e)}

# Searches for auctions matching the search_item and collects extra details.
async def search_auctions(search_item):
    found_auctions = []
    items = await load_items()  # Asynchronously load items

    normalized_search_item = normalize_string(search_item)

    # Loop over two pages of auctions.
    for page in range(2):
        auctions = await get_auctions(page)
        if not auctions.get('success', False):
            continue
        
        for auction in auctions['auctions']:
            normalized_item_name = normalize_string(auction['item_name'])
            
            if normalized_search_item in normalized_item_name and auction.get('bin', False):
                # Find the most specific match from the items JSON.
                best_match = None
                for item in items:
                    normalized_name = normalize_string(item['name'])
                    if normalized_search_item in normalized_name:
                        if not best_match or len(normalized_name) > len(normalize_string(best_match['name'])):
                            best_match = item

                image_url = best_match['png'] if best_match else None

                # Get seller name based on UUID.
                auctioneer_uuid = auction.get('auctioneer', None)
                seller_name = await get_username(auctioneer_uuid) if auctioneer_uuid else 'Unknown Seller'
                
                # Format the auction's posted time.
                auction_time = time_ago(auction['start'])

                # Safely convert starting_bid to an int.
                try:
                    starting_bid = int(float(auction.get("starting_bid", 0)))
                except (ValueError, TypeError):
                    starting_bid = 0

                # Process item_bytes (if available) to extract count information.
                item_bytes_base64 = auction.get('item_bytes')
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
                    "starting_bid": starting_bid,
                    "seller": seller_name,
                    "time_display": auction_time,
                    "image_url": image_url,
                    "count_section": count_int
                })

        # Respect rate-limiting by sleeping briefly.
        await asyncio.sleep(1)

    return found_auctions

# Serve the main page.
@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "results": []})

# API route for searching auctions with optional min_price and max_price constraints.
@app.get("/search")
async def search(
    search_item: str = Query(...),
    min_price: float = Query(None),
    max_price: float = Query(None)
):
    # Create a cache key that includes search term and price constraints.
    cache_key = f"{search_item}:{min_price}:{max_price}"
    cached_results = await cache.get(cache_key)
    if cached_results:
        return {"results": json.loads(cached_results)}

    # Search auctions based on the search term.
    auctions = await search_auctions(search_item)

    # Debug logging: print auction count and received price constraints.
    print(f"Auctions before filtering: {len(auctions)}")
    print(f"min_price: {min_price}, max_price: {max_price}")
    
    # Filter auctions to only include those whose starting_bid is between min_price and max_price.
    if min_price is not None:
        auctions = [auction for auction in auctions if auction["starting_bid"] >= min_price]
    if max_price is not None:
        auctions = [auction for auction in auctions if auction["starting_bid"] <= max_price]

    print(f"Auctions after filtering: {len(auctions)}")

    # Cache the filtered results for 60 seconds.
    await cache.set(cache_key, json.dumps(auctions), ttl=60)

    return {"results": auctions}
