PLANTATION RAINFALL DASHBOARD
=============================

This repository contains the data-pull scripts and local dashboard source code.
Generated CSV, JSON, PDF, report and cache files are intentionally excluded from
GitHub. Users do not need to import CSV files manually: the update scripts create
all required local data files.


FIRST-TIME SETUP
----------------

1. Install Python 3.10 or later.

2. Open PowerShell in this repository folder and install the required packages:

   python -m pip install -r requirements.txt

3. Create the private SAP credential file:

   Copy-Item sap_credentials.example.py sap_credentials.py

4. Open sap_credentials.py and replace the two placeholder values with the valid
   Syncera DEV and PROD client secrets.

   Do not commit or share sap_credentials.py. It is excluded by .gitignore.

5. Keep koordinat_estate_update.xlsx in the repository folder. It supplies the
   plantation coordinates needed for the Open-Meteo plantation pull.


RECOMMENDED: REFRESH EVERYTHING AND OPEN THE DASHBOARD
-----------------------------------------------------

Double-click:

   RUN_RAINFALL_DASHBOARD.bat

Alternatively, run this from PowerShell:

   python RUN_RAINFALL_DASHBOARD.py

This one-click workflow performs the following operations in order:

1. Pulls All Regions rainfall data from Open-Meteo.
2. Pulls All Regions temperature and soil-moisture data.
3. Pulls DEV and PROD plantation rainfall data from the SAP/Syncera API.
4. Pulls plantation Open-Meteo actual rainfall and the 14-day forecast.
5. Rebuilds the files used by the local dashboard.
6. Stops an older dashboard server started by this package, starts the refreshed
   dashboard server, and opens the dashboard.

The terminal will show the latest confirmed SAP actual date. A refresh is only
reported as successful after the downloaded files and rebuilt dashboard data pass
validation.


REFRESH DATA WITHOUT OPENING THE DASHBOARD
------------------------------------------

Run:

   python update_dashboard_data.py

This refreshes both data sections:

- All Regions: Open-Meteo rainfall, temperature and soil moisture.
- Inhouse Plantation: SAP rainfall plus plantation Open-Meteo actuals and forecast.

The dashboard data is rebuilt automatically. No manual CSV import is required.


LAUNCH THE DASHBOARD WITHOUT REFRESHING DATA
-------------------------------------------

Run:

   python dashboard_server.py

Then open:

   http://localhost:8080/

Jacob's current company-network link is:

   http://192.168.1.82:8080/

The 192.168.1.82 address is specific to that computer/network configuration. On a
different computer, use http://localhost:8080/ or that computer's own LAN IP address.
Keep the server process running while using the dashboard.


INDIVIDUAL UPDATE SCRIPTS
-------------------------

Normally use update_dashboard_data.py instead of running these separately.

- update_all_regions_data.py
  Refreshes the All Regions Open-Meteo datasets.

- SAP_rainfall/update_all_rainfall_dashboard.py
  Refreshes SAP rainfall, plantation Open-Meteo data and the plantation dashboard
  data files.

Example:

   python SAP_rainfall/update_all_rainfall_dashboard.py --workers 4


DAILY OPERATING PROCEDURE
-------------------------

1. Connect to the internet and company/VPN network required for SAP access.
2. Double-click RUN_RAINFALL_DASHBOARD.bat.
3. Wait for "All local dashboard data was refreshed successfully."
4. Confirm that the displayed "Latest PROD actual date" is reasonable.
5. Open or refresh http://localhost:8080/.

If the update fails, the existing live dashboard data is not replaced. Read the
failed-step message in the terminal, correct the connection, credential or API issue,
and run the same launcher again.


SECURITY AND REPOSITORY CONTENT
-------------------------------

- sap_credentials.py contains live secrets and must remain private.
- sap_credentials.example.py is the safe template stored on GitHub.
- Generated CSV/JSON/PDF files, caches and logs are excluded from GitHub.
- Email generation, PDF generation and hosted-Site deployment code are not included
  in this repository.
