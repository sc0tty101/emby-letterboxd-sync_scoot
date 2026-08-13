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
CONFIG_FILE = os.path.join(os.environ.get("CONFIG_DIR", "/data"), "config.json")

def init_dependencies():
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
    with open(CONFIG_FILE, 'w') as file:
        json.dump(config, file, indent=4)

def init_config():
    emby_url = os.environ.get("EMBY_URL")
    emby_api_key = os.environ.get("EMBY_API_KEY")
    interval = os.environ.get("SYNC_INTERVAL_MS", "300000")
    letterboxd_username = os.environ.get("LETTERBOXD_USERNAME")
    emby_username = os.environ.get("EMBY_USERNAME")

    if not emby_url or not emby_api_key:
        raise RuntimeError("Missing required environment variables: EMBY_URL and EMBY_API_KEY must be set.")
    if not letterboxd_username or not emby_username:
        raise RuntimeError("Missing required environment variables: LETTERBOXD_USERNAME and EMBY_USERNAME must be set.")

    print("Building config from environment variables...")

    config = {
        "emby_url": emby_url,
        "emby_api_key": emby_api_key,
        "sync_interval_ms": int(interval),
        "users": []
    }

    # Auto-resolve Emby user ID and create playlist
    user_id = get_emby_user_id(emby_username, config)
    if not user_id:
        raise RuntimeError(f"Could not find Emby user '{emby_username}'. Check your EMBY_USERNAME and EMBY_URL.")

    playlist_name = f"{letterboxd_username}'s Letterboxd Watchlist"
    playlist_id = init_playlist(user_id, playlist_name, config)
    if not playlist_id:
        raise RuntimeError(f"Could not create or find playlist for user '{emby_username}'.")

    config["users"].append({
        "letterboxd_username": letterboxd_username,
        "emby_username": emby_username,
        "user_id": user_id,
        "playlist_id": playlist_id
    })

    save_config(config)
    print("Configuration saved.")
    return config

def get_letterboxd_watchlist(username):
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
    playlist_url = f"{config['emby_url']}/Users/{user_id}/Items?IncludeItemTypes=Playlist&Recursive=true&api_key={config['emby_api_key']}"
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

    name_to_runtime_ids = {}
    for movie in movies:
        name = movie["Name"].strip().lower()
        runtime_ticks = movie.get("RunTimeTicks")
        if runtime_ticks is None:
            logging.warning(f"Movie '{movie['Name']}' does not have a RunTimeTicks. Skipping.")
            continue
        runtime_minutes = runtime_ticks // (10**7 * 60)
        if name in name_to_runtime_ids:
            name_to_runtime_ids[name].append((runtime_minutes, movie["Id"]))
        else:
            name_to_runtime_ids[name] = [(runtime_minutes, movie["Id"])]

    try:
        playlist_items_url = f"{config['emby_url']}/Playlists/{playlist_id}/Items?api_key={config['emby_api_key']}"
        playlist_response = requests.get(playlist_items_url)
        playlist_response.raise_for_status()
        current_playlist = playlist_response.json().get("Items", [])
        current_playlist_title_runtimes = set()
        for item in current_playlist:
            name = item["Name"].strip().lower()
            runtime_ticks = item.get("RunTimeTicks")
            if runtime_ticks is None:
                logging.warning(f"Playlist item '{item['Name']}' does not have a RunTimeTicks. Skipping.")
                continue
            runtime_minutes = runtime_ticks // (10**7 * 60)
            current_playlist_title_runtimes.add((name, runtime_minutes))
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching current playlist items: {e}")
        return
    except ValueError:
        logging.error("Error parsing JSON response from Emby playlist items.")
        return

    items_to_add = []
    for title in watchlist_titles:
        name_key = title.strip().lower()
        if name_key in name_to_runtime_ids:
            emby_entries = name_to_runtime_ids[name_key]
            duplicate_found = False
            for runtime, emby_id in emby_entries:
                if (name_key, runtime) in current_playlist_title_runtimes:
                    duplicate_found = True
                    break
            if not duplicate_found:
                runtime_to_add, emby_id_to_add = emby_entries[0]
                items_to_add.append(emby_id_to_add)
        else:
            logging.warning(f"No matching Emby entry found for movie: {title}")

    logging.info(f"Total new movies to add: {len(items_to_add)}")

    if items_to_add:
        add_items_url = f"{config['emby_url']}/Playlists/{playlist_id}/Items"
        params = {
            "Ids": ",".join(items_to_add),
            "UserId": user_id,
            "api_key": config["emby_api_key"]
        }
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

def run_daemon_mode(config):
    interval_ms = config.get("sync_interval_ms", 300000)
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

def main():
    config = load_config()
    run_daemon_mode(config)

if __name__ == "__main__":
    init_dependencies()
    main()
