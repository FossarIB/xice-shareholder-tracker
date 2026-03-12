# XICE Shareholder Tracker

Monitors the **top 20 shareholders** of all companies on Nasdaq Iceland's Main Market (XICE) on a daily basis. Detects changes and sends email notifications + generates a local web dashboard.

## What It Tracks

| Change Type | Description |
|---|---|
| **New Entries** | Shareholders who appeared in a company's top 20 |
| **Exits** | Shareholders who dropped out of the top 20 |
| **Ownership Shifts** | Percentage changes above a configurable threshold (default: 0.5pp) |

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure email (optional but recommended)

```bash
cp config.example.yaml config.yaml
# Edit config.yaml with your SMTP settings and recipient list
```

**Gmail users:** You'll need to [create an App Password](https://support.google.com/accounts/answer/185833) since Gmail doesn't allow plain login from scripts.

### 3. Run a scan

```bash
# Single scan — scrapes all companies, diffs against yesterday, sends email
python tracker.py

# Run on a daily schedule (default: 18:00 UTC / IST)
python tracker.py --schedule

# Custom schedule time
python tracker.py --schedule --time 17:30

# Regenerate the dashboard without scanning
python tracker.py --dashboard

# Send a test email to verify your config
python tracker.py --test-email
```

### 4. View the dashboard

Open `dashboard/index.html` in any browser. The dashboard loads from `dashboard/data.json` which is updated after each scan.

To share with coworkers on your local network:
```bash
cd dashboard
python -m http.server 8080
# Then share: http://your-ip:8080
```

## Project Structure

```
xice-shareholder-tracker/
├── tracker.py              # Main application
├── config.yaml             # Your configuration (create from example)
├── config.example.yaml     # Configuration template
├── requirements.txt        # Python dependencies
├── README.md               # This file
├── data/
│   ├── snapshots/          # Daily JSON snapshots (YYYY-MM-DD.json)
│   └── email_*.html        # Archived email notifications
├── dashboard/
│   ├── index.html          # Local web dashboard
│   └── data.json           # Dashboard data (auto-generated)
└── tracker.log             # Application log
```

## Data Sources

The tracker scrapes shareholder data from two types of sources:

1. **Primary — Company IR pages:** Each XICE company's investor relations website, which typically publishes their shareholder registry. These are updated at varying frequencies (some daily, some weekly/monthly).

2. **Fallback — keldan.is:** Iceland's leading financial data platform, which aggregates shareholder data from the Nasdaq CSD registry.

3. **Reference — Nasdaq CSD Monthly Excel:** Nasdaq CSD Iceland publishes a monthly Excel file with the top 20 shareholders of all listed companies. This is useful for validation but too infrequent for daily tracking.

## Customizing Scrapers

The generic HTML parser works for most Icelandic IR pages that display shareholders in HTML tables. If a company uses an unusual format, you can add a custom scraper:

1. Open `tracker.py`
2. Find the company in the `XICE_COMPANIES` list
3. Update the `shareholder_url` if the URL has changed
4. If needed, write a custom parser function and set `"scraper": "custom_function_name"`

## Running as a Service

### Linux (systemd)

Create `/etc/systemd/system/xice-tracker.service`:

```ini
[Unit]
Description=XICE Shareholder Tracker
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/xice-shareholder-tracker
ExecStart=/usr/bin/python3 tracker.py --schedule
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable xice-tracker
sudo systemctl start xice-tracker
```

### macOS (launchd)

Create `~/Library/LaunchAgents/com.xice.tracker.plist` — or simply use `cron`:
```bash
crontab -e
# Add: 0 18 * * * cd /path/to/xice-shareholder-tracker && python3 tracker.py
```

### Windows (Task Scheduler)

Use Task Scheduler to run `python tracker.py` daily at your preferred time.

## Notes

- Iceland uses UTC year-round (no daylight saving), so 18:00 UTC = 18:00 local time.
- The shareholder registry reflects registered holders, not necessarily beneficial owners.
- Data from company IR pages may lag behind actual ownership changes.
- The tracker respects a configurable delay between requests (default: 2 seconds) to be polite to servers.
- All data is stored locally — nothing is sent to external services except the email notifications you configure.

## License

MIT — Use freely, modify as needed.
