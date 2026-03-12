# Etsy Bulk Video Uploader

A lightweight, local web application to bulk upload a single MP4 video to multiple active Etsy listings using the Etsy Open API v3. 

Built with a Python/Flask backend to handle the complex OAuth 2.0 (PKCE) flow and a pure HTML/CSS/JS frontend that mimics Etsy's clean UI.

## Prerequisites
* Python 3.8+ installed on your machine.
* An approved app on the [Etsy Developer Portal](https://www.etsy.com/developers/your-apps/).

## Step 1: Etsy API Setup
Before running the application, you need to register a custom app on Etsy to get your API credentials. 

1. Create a new app on the Etsy Developer portal.
   * **App Type:** Seller Tools
   * **Users:** Just myself or colleagues (Personal Access)
   * **Commercial:** No
2. Once approved, Etsy will give you a **Keystring**. This is your `Client ID`.
3. In your Etsy App settings, scroll down to **Callback URLs** and add:
   * `http://localhost:8080/callback`

## Step 2: Local Configuration
You need a local configuration file to securely store your credentials. 

1. Duplicate the `config.local.example.json` file and rename it to `config.local.json`.
2. Open `config.local.json` and update the following fields:
   * `etsy_client_id`: Paste your Etsy **Keystring** here.
   * `flask_secret_key`: Replace the default text with a random string of characters (this secures your local browser session).

*(Note: `config.local.json` is ignored by git to keep your credentials safe).*

## Step 3: Installation & Running
1. Open your terminal and install the required Python packages:
   ```bash
   python3 -m pip install -r requirements.txt
    ```
2. Start the local Flask server:
    ```bash
    python3 app.py
    ```
3. Open your web browser and navigate to:
    ```http://localhost:8080
    ```

## Usage Guide
1. Connect to Etsy: Click the primary button to authenticate via OAuth.

2. Select Video: Choose the .mp4 file you want to upload (must be under 100MB).

3. Choose Listings: Click "Refresh" to pull your active listings, then check the boxes for the ones you want to update.

4. Upload: Click the upload button. The app will process each listing one by one, deleting any existing videos first, and pausing for 2 seconds between each upload to respect Etsy's rate limits. Watch the Status Log for real-time progress!