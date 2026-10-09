#!/bin/sh
# Alerts loop in the background (it exits immediately if no notification channel is configured),
# then the web app in the foreground.
python run_alerts.py --loop 30 &
exec streamlit run app.py --server.port 8080 --server.address 0.0.0.0 --server.headless true
