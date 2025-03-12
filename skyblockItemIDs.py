import requests
import json

# Define the API endpoint
url = "https://api.hypixel.net/v2/resources/skyblock/items"

# Fetch the data from the API
response = requests.get(url)

# Check if the response was successful
if response.status_code == 200:
    data = response.json()
    
    # Extract the name and id from the items
    items = [{"name": item["name"], "id": item["id"]} for item in data["items"]]

    # Output the list of items to a JSON file
    with open('skyblock_item_ids.json', 'w') as json_file:
        json.dump(items, json_file, indent=4)  # indent for pretty formatting
    
    print("Data has been written to items.json")
else:
    print("Failed to retrieve data from the API.")