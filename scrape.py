import requests
from bs4 import BeautifulSoup
import json
import sys
import time
from geopy.geocoders import Nominatim
import re

def get_nearest_intersection(lat, lon, geolocator):
    """
    Finds the nearest intersection to a given lat/lon pair using reverse geocoding.
    Note: Nominatim is not always precise with intersections. This is a best-effort attempt.
    """
    if lat is None or lon is None:
        return ""

    try:
        # Perform a reverse geocode lookup. language=en ensures we get English results.
        location = geolocator.reverse((lat, lon), exactly_one=True, language='en', timeout=5)

        if location and location.raw and 'address' in location.raw:
            address = location.raw['address']

            # Nominatim may return 'road', 'street', 'pedestrian', etc.
            road = address.get('road') or address.get('street') or address.get('pedestrian', '')

            # Sometimes a suburb or neighborhood is more useful if a road isn't found
            suburb = address.get('suburb', '')

            # Heuristic: Check if the returned address looks like an intersection
            # This is not foolproof with Nominatim.
            if road and ('&' in road or '/' in road):
                return road

            # Fallback: Construct a string with what we have
            if road and suburb:
                return f"{road}, {suburb}"
            elif road:
                return road
            elif suburb:
                return suburb
        return ""

    except Exception as e:
        print(f"-> Reverse Geocoding Error: {e}", file=sys.stderr)
        return ""

def scrape_incidents():
    """
    Fetches the Richmond, VA active calls page and scrapes the main table.
    Also geocodes the location of each incident.
    """
    URL = "https://apps.richmondgov.com/applications/activecalls/Home/ActiveCalls"
    
    # Initialize geocoder (Nominatim is free, requires a user agent)
    # We add a 1.1 second delay between queries to respect their terms of service.
    geolocator = Nominatim(user_agent="richmond_incident_mapper_v1")
    
    # Set headers to mimic a real browser request
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    print(f"Attempting to fetch data from {URL}...", file=sys.stderr)

    try:
        # Fetch the page content
        response = requests.get(URL, headers=headers, timeout=10)
        
        # Check for HTTP errors
        response.raise_for_status() 
        print("Successfully fetched page.", file=sys.stderr)

        # Parse the HTML content
        soup = BeautifulSoup(response.text, 'html.parser')

        table = soup.find('table')

        if not table:
            print("Error: Could not find the data table.", file=sys.stderr)
            return None

        incidents = []
        
        for row in table.find_all('tr')[1:]:
            cells = row.find_all('td')
            
            if len(cells) >= 7:
                incident = {
                    'type_general': cells[1].text.strip(),
                    'dispatch_time': cells[0].text.strip(),
                    'box_no': cells[2].text.strip(),
                    'type_specific': cells[4].text.strip(),
                    'street': cells[5].text.strip(),
                    'status': cells[6].text.strip(),
                    'cross_street': '',
                    'nearest_intersection': '',
                    'location_township': cells[2].text.strip()
                }

                # --- Geocoding Step ---
                cleaned_street = incident['street'].replace('-BLK', '').replace('/', ' and ')
                cleaned_street = re.sub(r'\s+RICH$', '', cleaned_street).strip()

                # --- Handle pre-geocoded LL(...) addresses ---
                if cleaned_street.startswith('LL('):
                    match = re.search(r'LL\(([^,]+),([^)]+)\)', cleaned_street)
                    if match:
                        lon_dms = match.group(1).strip()
                        lat_dms = match.group(2).strip()

                        def dms_to_dd(dms):
                            parts = [float(p) for p in dms.split(':')]
                            dd = abs(parts[0]) + parts[1]/60 + parts[2]/3600
                            if parts[0] < 0:
                                return -dd
                            return dd
                        
                        try:
                            incident['lng'] = dms_to_dd(lon_dms)
                            incident['lat'] = dms_to_dd(lat_dms)
                            print(f"-> Parsed from LL: ({incident['lat']}, {incident['lng']})", file=sys.stderr)

                            # --- Reverse Geocode for Intersection ---
                            intersection = get_nearest_intersection(incident['lat'], incident['lng'], geolocator)
                            if intersection:
                                incident['nearest_intersection'] = intersection
                                print(f"-> Nearest Intersection: {intersection}", file=sys.stderr)
                            else:
                                print("-> No intersection found.", file=sys.stderr)

                        except (ValueError, IndexError):
                             print(f"-> Warning: Could not parse LL address: {cleaned_street}", file=sys.stderr)
                             incident['lat'] = None
                             incident['lng'] = None
                    else:
                        print(f"-> Warning: Could not parse LL address: {cleaned_street}", file=sys.stderr)
                        incident['lat'] = None
                        incident['lng'] = None
                    
                    incidents.append(incident)
                    continue # Skip Nominatim geocoding

                # Check if it's an intersection
                if ' and ' in cleaned_street:
                    # It is. Split it and take just the first street.
                    address_to_geocode = cleaned_street.split(' and ')[0]
                elif "RICH: @" in cleaned_street and "BETWEEN" in cleaned_street:
                    # Handle "RICH: @<street> BETWEEN <cross_street_1> & <cross_street_2>"
                    try:
                        main_street = cleaned_street.split('@')[1].split('BETWEEN')[0].strip()
                        main_street = re.sub(r'\s(NB|SB)$', '', main_street) # Remove NB/SB
                        address_to_geocode = main_street
                    except IndexError:
                        address_to_geocode = cleaned_street # Fallback
                else:
                    # It's a block or regular address
                    address_to_geocode = cleaned_street

                # Now, add the city and state
                full_address = f"{address_to_geocode}, Richmond, VA"

                print(f"Geocoding: {full_address}", file=sys.stderr)
                
                try:
                    location = geolocator.geocode(full_address, timeout=5)
                    if location:
                        incident['lat'] = location.latitude
                        incident['lng'] = location.longitude
                        print(f"-> Found: ({location.latitude}, {location.longitude})", file=sys.stderr)

                        # --- Reverse Geocode for Intersection ---
                        intersection = get_nearest_intersection(incident['lat'], incident['lng'], geolocator)
                        if intersection:
                            incident['nearest_intersection'] = intersection
                            print(f"-> Nearest Intersection: {intersection}", file=sys.stderr)
                        else:
                            print("-> No intersection found.", file=sys.stderr)

                    else:
                        incident['lat'] = None
                        incident['lng'] = None
                        print(f"-> Warning: Could not geocode address: {full_address}", file=sys.stderr)
                except Exception as e:
                    print(f"-> Geocoding Error: {e}", file=sys.stderr)
                    incident['lat'] = None
                    incident['lng'] = None

                incidents.append(incident)
                
                # --- Rate Limiting ---
                # IMPORTANT: Pause for 1.1s to respect Nominatim's (1 req/sec) free usage policy.
                time.sleep(1.1) 
        
        print(f"Found and geocoded {len(incidents)} incidents.", file=sys.stderr)
        return incidents

    except requests.exceptions.HTTPError as errh:
        print(f"Http Error: {errh}", file=sys.stderr)
    except requests.exceptions.ConnectionError as errc:
        print(f"Error Connecting: {errc}", file=sys.stderr)
    except requests.exceptions.Timeout as errt:
        print(f"Timeout Error: {errt}", file=sys.stderr)
    except requests.exceptions.RequestException as err:
        print(f"An unexpected error occurred: {err}", file=sys.stderr)
    
    return None

if __name__ == "__main__":
    incident_data = scrape_incidents()
    
    if incident_data:
        # Convert the list of incidents to a JSON string and print it
        json_output = json.dumps(incident_data, indent=2)
        print(json_output)

        # ---- Write to incidents.json ----
        output_file = "incidents.json"
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(json_output)
            print(f"--- INFO: Incident data written to '{output_file}'. ---", file=sys.stderr)
        except Exception as e:
            print(f"Could not write to '{output_file}': {e}", file=sys.stderr)
    else:
        print("No incident data was scraped.", file=sys.stderr)

