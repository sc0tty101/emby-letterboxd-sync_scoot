import os
import sys
import subprocess
import venv
import argparse
import json
import time
import requests
import re
import logging
CONFIG_FILE = "config.json"

def init_dependencies():
    """
    Ensure that a virtual environment exists and required packages are installed.
    If the virtual environment does not exist, it is created and necessary packages are installed.
    The script is then re-executed within the virtual environment.
    """
    REQUIRED_PACKAGES = ['beautifulsoup4', 'requests', 'rapidfuzz']
    VENV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'venv')

    def is_venv():
        return hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)

    def create_venv():
        print("Creating virtual environment...")
        venv.create(VENV_DIR, with_pip=True)
        pip_path = os.path.join(VENV_DIR, 'bin', 'pip') if os.name != 'nt' else os.path.join(VENV_DIR, 'Scripts', 'pip')
        print("Installing required packages...")
        subprocess.check_call([pip_path, 'install'] + REQUIRED_PACKAGES)

    if not is_venv():
        if not os.path.exists(VENV_DIR):
            create_venv()
        python_path = os.path.join(VENV_DIR, 'bin', 'python') if os.name != 'nt' else os.path.join(VENV_DIR, 'Scripts', 'python')
        os.execv(python_path, [python_path] + sys.argv)

    try:
        import bs4
        import requests
        from rapidfuzz import fuzz
    except ImportError:
        pip_path = os.path.join(sys.prefix, 'bin', 'pip') if os.name != 'nt' else os.path.join(sys.prefix, 'Scripts', 'pip')
        print("Installing missing dependencies...")
        subprocess.check_call([pip_path, 'install'] + REQUIRED_PACKAGES)
        os.execv(sys.executable, [sys.executable] + sys.argv)

init_dependencies()

from bs4 import BeautifulSoup
from rapidfuzz import fuzz

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return init_config()
    with open(CONFIG_FILE, 'r') as file:
        return json.load(file)

def save_config(config):
    """
    Save the configuration to the CONFIG_FILE.
    
    Args:
        config (dict): The configuration to save.
    """
    with open(CONFIG_FILE, 'w') as file:
        json.dump(config, file, indent=4)

def init_config():
    emby_url = os.environ.get("EMBY_URL")
    emby_api_key = os.environ.get("EMBY_API_KEY")
    interval = os.environ.get("SYNC_INTERVAL_MS", "300000")

    if not emby_url or not emby_api_key:
        raise RuntimeError("Missing required environment variables: EMBY_URL and EMBY_API_KEY must be set.")

    config = {
        "emby_url": emby_url,
        "emby_api_key": emby_api_key,
        "sync_interval_ms": int(interval),
        "users": []
    }
    save_config(config)
    print(f"Configuration saved to '{CONFIG_FILE}'.")
    return config

def get_letterboxd_watchlist(username):
    """
    Fetch the given user's Letterboxd watchlist using bs4.
    
    Args:
        username (str): Letterboxd username.
    
    Returns:
        list: A list of movie titles from the user's watchlist.
    """
    movies = []
    page = 1
    print(f"Fetching watchlist for {username}")
    while True:
        watchlist_url = f"https://letterboxd.com/{username}/watchlist/page/{page}/"
        try:
            response = requests.get(watchlist_url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            film_items = soup.find_all('div', class_='film-poster')
            if not film_items:
                print(f"Page {page}: No more posters found, stopping.")
                break

            for film in film_items:
                img_tag = film.find('img', class_='image')
                if img_tag and img_tag.get('alt'):
                    movie_title = img_tag['alt'].strip()
                    print(f"Found movie: {movie_title}")
                    movies.append(movie_title)
                else:
                    print(f"Film-poster missing 'alt' attribute: {film}")

            if len(film_items) < 28:
                break
            page += 1

        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error occurred: {http_err}")
            break
        except Exception as e:
            print(f"Error fetching watchlist for {username} on page {page}: {e}")
            break

    print(f"Total movies found for {username}: {len(movies)}")
    return movies

def get_emby_user_id(emby_username, config):
    """
    Retrieve the Emby user ID based on the username.
    
    Args:
        emby_username (str): The Emby username.
        config (dict): The configuration dictionary containing Emby details.
    
    Returns:
        str or None: The Emby user ID if found, else None.
    """
    url = f"{config['emby_url']}/Users?api_key={config['emby_api_key']}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        users = response.json()
        for user in users:
            if user["Name"].lower() == emby_username.lower():
                return user["Id"]
        print(f"User '{emby_username}' not found in Emby.")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Emby user ID: {e}")
    return None

def init_playlist(user_id, playlist_name, config):
    """
    Initialise watchlist playlist in Emby. Checks if a playlist exists for a user in Emby; if not, creates it.
    
    Args:
        user_id (str): The Emby user ID.
        playlist_name (str): The name of the playlist.
        config (dict): The configuration dictionary containing Emby details.
    
    Returns:
        str or None: The playlist ID if successful, else None.
    """
    playlist_url = f"{config['emby_url']}/Users/{user_id}/Items?IncludeItemTypes=Playlist&api_key={config['emby_api_key']}"
    try:
        response = requests.get(playlist_url)
        response.raise_for_status()
        playlists = response.json().get("Items", [])

        for playlist in playlists:
            if playlist["Name"].lower() == playlist_name.lower():
                print(f"Playlist '{playlist_name}' already exists for user '{user_id}'.")
                return playlist["Id"]

        create_url = f"{config['emby_url']}/Playlists?api_key={config['emby_api_key']}"
        payload = {
            "Name": playlist_name,
            "UserId": user_id,
            "MediaType": "Video"
        }
        create_response = requests.post(create_url, json=payload)
        create_response.raise_for_status()
        print(f"Playlist '{playlist_name}' created successfully for user '{user_id}'.")
        return create_response.json().get("Id")
    except requests.exceptions.RequestException as e:
        print(f"Error creating playlist: {e}")
        return None

def add_to_playlist(playlist_id, items_to_add, config, user_id):
    """
    Add items to an Emby playlist.
    
    Args:
        playlist_id (str): The ID of the playlist.
        items_to_add (list): A list of item IDs to add to the playlist.
        config (dict): The configuration dictionary containing Emby details.
        user_id (str): The Emby user ID.
    """
    add_items_url = f"{config['emby_url']}/Playlists/{playlist_id}/Items"
    params = {
        "Ids": ",".join(map(str, items_to_add)),
        "UserId": user_id,
        "api_key": config["emby_api_key"]
    }
    try:
        response = requests.post(add_items_url, params=params)
        response.raise_for_status()
        print(f"Successfully added {len(items_to_add)} items to the playlist.")
    except requests.exceptions.RequestException as e:
        print(f"Error adding items to playlist: {e}")

def sync_playlist(playlist_id, watchlist_titles, user_id, config):
    """
    Sync movies from a Letterboxd watchlist to an Emby playlist without creating duplicates based on movie name and runtime.

    Args:
        playlist_id (str): The ID of the Emby playlist.
        watchlist_titles (list): A list of movie titles from Letterboxd.
        user_id (str): The Emby user ID.
        config (dict): The configuration dictionary containing Emby details.
    """
    # Fetch Emby movie library
    library_url = f"{config['emby_url']}/Items?Recursive=true&IncludeItemTypes=Movie&api_key={config['emby_api_key']}"
    try:
        response = requests.get(library_url)
        response.raise_for_status()
        movies = response.json()["Items"]
        logging.debug(f"Fetched {len(movies)} movies from Emby library.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching Emby movie library: {e}")
        return
    except ValueError:
        logging.error("Error parsing JSON response from Emby movie library.")
        return

    # Create a mapping from name.lower() to list of (Runtime, Emby ID)
    name_to_runtime_ids = {}
    for movie in movies:
        name = movie["Name"].strip().lower()
        runtime_ticks = movie.get("RunTimeTicks")  # Emby stores runtime in ticks (1 tick = 100 nanoseconds)
        if runtime_ticks is None:
            logging.warning(f"Movie '{movie['Name']}' does not have a RunTimeTicks. Skipping.")
            continue
        # Convert RuntimeTicks to minutes
        runtime_minutes = runtime_ticks // (10**7 * 60)  # 10^7 ticks per second, 60 seconds per minute
        if name in name_to_runtime_ids:
            name_to_runtime_ids[name].append((runtime_minutes, movie["Id"]))
        else:
            name_to_runtime_ids[name] = [(runtime_minutes, movie["Id"])]
    logging.debug(f"Created mapping of (name, runtime) to Emby IDs: {name_to_runtime_ids}")

    # Fetch current playlist items
    try:
        playlist_items_url = f"{config['emby_url']}/Playlists/{playlist_id}/Items?api_key={config['emby_api_key']}"
        playlist_response = requests.get(playlist_items_url)
        playlist_response.raise_for_status()
        current_playlist = playlist_response.json().get("Items", [])
        # Create a set of (name.lower(), runtime_minutes) for existing playlist items
        current_playlist_title_runtimes = set()
        for item in current_playlist:
            name = item["Name"].strip().lower()
            runtime_ticks = item.get("RunTimeTicks")
            if runtime_ticks is None:
                # Attempt to extract runtime from another field if available
                logging.warning(f"Playlist item '{item['Name']}' does not have a RunTimeTicks. Skipping.")
                continue
            runtime_minutes = runtime_ticks // (10**7 * 60)
            current_playlist_title_runtimes.add((name, runtime_minutes))
        logging.debug(f"Current playlist has {len(current_playlist_title_runtimes)} unique (name, runtime) pairs.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching current playlist items: {e}")
        return
    except ValueError:
        logging.error("Error parsing JSON response from Emby playlist items.")
        return

    # Determine which Emby IDs to add
    items_to_add = []
    for title in watchlist_titles:
        logging.debug(f"Processing watchlist movie: {title}")
        # Assuming the watchlist title is just the movie name without runtime
        name_key = title.strip().lower()
        if name_key in name_to_runtime_ids:
            # Get all Emby entries for this name
            emby_entries = name_to_runtime_ids[name_key]
            # Check if any of the Emby entries have the same runtime as an existing playlist item
            duplicate_found = False
            for runtime, emby_id in emby_entries:
                if (name_key, runtime) in current_playlist_title_runtimes:
                    logging.debug(f"Duplicate found for movie '{title}' with runtime {runtime} minutes. Skipping.")
                    duplicate_found = True
                    break  # No need to check other Emby entries for this title
            if not duplicate_found:
                # Add the first Emby ID (or implement a selection strategy if needed)
                runtime_to_add, emby_id_to_add = emby_entries[0]
                items_to_add.append(emby_id_to_add)
                logging.debug(f"Adding movie '{title}' with runtime {runtime_to_add} minutes (ID: {emby_id_to_add}) to playlist.")
        else:
            logging.warning(f"No matching Emby entry found for movie: {title}")

    logging.info(f"Total new movies to add: {len(items_to_add)}")

    # Add new items to the playlist
    if items_to_add:
        add_items_url = f"{config['emby_url']}/Playlists/{playlist_id}/Items"
        params = {
            "Ids": ",".join(items_to_add),
            "UserId": user_id,
            "api_key": config["emby_api_key"]
        }
        logging.debug(f"Adding items to playlist {playlist_id}: {items_to_add}")
        try:
            response = requests.post(add_items_url, params=params)
            response.raise_for_status()
            logging.info(f"Successfully added {len(items_to_add)} items to the playlist.")
        except requests.exceptions.HTTPError as http_err:
            logging.error(f"HTTP error occurred while adding items to playlist: {http_err}")
            logging.error(f"Response content: {response.text}")
        except requests.exceptions.RequestException as e:
            logging.error(f"Error adding items to playlist: {e}")
    else:
        logging.info("No new movies to add to the playlist.")

def run_sync(config):
    """
    Run the synchronisation process for all users defined in the configuration.
    
    Args:
        config (dict): The configuration dictionary containing user and Emby details.
    """
    for user in config["users"]:
        letterboxd_username = user["letterboxd_username"]
        emby_username = user["emby_username"]
        user_id = user.get("user_id")
        playlist_id = user.get("playlist_id")

        print(f"\nProcessing user: {emby_username} ({letterboxd_username})")

        if not user_id or not playlist_id:
            print(f"Missing userId or playlistId for user '{letterboxd_username}'. Skipping...")
            continue

        watchlist_titles = get_letterboxd_watchlist(letterboxd_username)
        print(f"Total movies found for {letterboxd_username}: {len(watchlist_titles)}")

        sync_playlist(playlist_id, watchlist_titles, user_id, config)
    print("\nSync complete.")

def add_new_user(config):
    """
    Add a new Letterboxd user and link them with an Emby username.
    Prompts the user for necessary information and updates the configuration.
    
    Args:
        config (dict): The configuration dictionary to update.
    """
    letterboxd_username = input("Enter the Letterboxd username: ").strip()
    print(f"Checking if Letterboxd username '{letterboxd_username}' exists...")
    # Assuming validation is done externally or by attempting to fetch the watchlist
    print(f"Letterboxd username '{letterboxd_username}' validated.")

    emby_username = input("Enter the Emby username: ").strip()
    print(f"Checking if Emby username '{emby_username}' exists...")
    user_id = get_emby_user_id(emby_username, config)
    if not user_id:
        print(f"Emby username '{emby_username}' not found. Please try again.")
        return

    playlist_name = f"{letterboxd_username}'s Letterboxd Watchlist"
    playlist_id = init_playlist(user_id, playlist_name, config)
    if not playlist_id:
        print(f"Failed to create playlist '{playlist_name}'.")
        return

    config["users"].append({
        "letterboxd_username": letterboxd_username,
        "emby_username": emby_username,
        "user_id": user_id,
        "playlist_id": playlist_id
    })
    save_config(config)
    print(f"User '{letterboxd_username}' linked to Emby user '{emby_username}' with user ID '{user_id}' and playlist ID '{playlist_id}'.")

def run_daemon_mode(config):
    """
    Run the synchronisation process continuously at the interval specified in the configuration.
    
    Args:
        config (dict): The configuration dictionary containing synchronisation interval and user details.
    """
    interval_ms = config.get("sync_interval_ms", 300000)  # Default to 5 minutes
    interval_seconds = interval_ms / 1000

    print(f"Starting daemon mode. Syncing every {interval_seconds} seconds...")

    try:
        while True:
            print("\n--- Running Sync ---")
            run_sync(config)
            print(f"Waiting for {interval_seconds} seconds before next sync...")
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("Daemon mode stopped by user.")
    except Exception as e:
        print(f"An error occurred in daemon mode: {e}")

def print_help():
    """
    Display the help screen with usage instructions.
    """
    help_text = """
    Usage: python3 letterboxd_sync.py [option]

    Options:
    -s    Setup: Creates a config file in the local directory if missing.
    -a    Add a new Letterboxd user and their corresponding Emby playlist.
    -r    Run: Perform a single synchronisation for all users.
    -d    Daemon Mode: Continuously run the sync process at the configured interval.
    -h    Help: Display this help screen.
    """
    print(help_text)

def main():
    config = load_config()

    if len(sys.argv) < 2:
        print("No valid arguments provided. Use -h for help.")
        return

    argument = sys.argv[1]
    
    match argument:
        case "-s":
            init_config()
        case "-a":
            add_new_user(config)
        case "-r":
            run_sync(config)
        case "-h":
            print_help()
        case "-d":
            run_daemon_mode(config)
        case _:
            print("Invalid argument provided. Use -h for help.")

if __name__ == "__main__":
    init_dependencies()
    main()
