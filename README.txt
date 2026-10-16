LOCAL RAINFALL DASHBOARD SOURCE CODE

Double-click RUN_RAINFALL_DASHBOARD.bat to:
1. Pull All Regions rainfall, temperature and soil-moisture data from Open-Meteo.
2. Pull DEV and PROD plantation rainfall from SAP.
3. Pull plantation actual and 14-day forecast rainfall from Open-Meteo.
4. Rebuild the local dashboard data.
5. Start and open http://192.168.1.82:8080/.

This source folder intentionally excludes generated CSV/PDF files, PDF generation,
email generation, hosted-site deployment code, reports and caches. Data files are
created locally when the runner is used.

PRIVATE: sap_credentials.py contains live SAP API secrets. Do not share or commit it.
For GitHub, commit sap_credentials.example.py but not sap_credentials.py. After cloning,
the IT team should copy the example to sap_credentials.py and enter the two secrets.
