import json

# Path to your original JSON file
json_file_path = 'skyblock_item_ids(ultimate_path).json'

# Read the JSON file
with open(json_file_path, 'r') as file:
    data = json.load(file)

# Loop through each entry and update the 'png' field
for item in data:
    if 'png' in item:
        del item["png"]

# Write the updated data back to the JSON file
with open(json_file_path, 'w') as file:
    json.dump(data, file, indent=4)

print("Paths updated successfully!")
