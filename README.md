# Android Performance Dashboard

A modern, real-time Android diagnostics and cleaning tool with a beautiful dark mode dashboard. Monitor CPU, RAM, thermal, and more, kill heavy apps, and clear cache—all from your browser.

---

## 🚀 Features
- **Live Dashboard:** Real-time stats for CPU, RAM, thermal sensors, and more
- **Dark Mode:** Modern, visually stunning UI inspired by premium analytics dashboards
- **Memory Health Alerts:** Get warnings when your device is low on RAM
- **Kill App:** Instantly kill heavy apps from the dashboard
- **Clear Cache:** One-click cache cleaning for all apps
- **Session Recording:** Record and export monitoring sessions
- **Responsive Design:** Works on desktop and mobile

---

## 📦 Requirements
- Python 3.7+
- [ADB (Android Debug Bridge)](https://developer.android.com/tools/adb) installed and on your PATH
- Android device with USB debugging enabled
- Python packages: `flask`, `flask-cors`

Install requirements:
```bash
pip install flask flask-cors
```

---

## ⚙️ Setup & Usage

### 1. **Connect Your Device**
- Enable USB debugging on your Android phone
- Connect via USB and authorize your computer

### 2. **Run the Dashboard**
```bash
python dashboard.py
```
- The dashboard will open automatically in your browser (or visit [http://localhost:5000](http://localhost:5000))

### 3. **Dashboard Controls**
- **Start Monitoring:** Begin live session (updates every second)
- **Stop & Export:** End session and save results
- **Clear Cache:** Free up space by clearing all app caches
- **Kill App:** Instantly kill any app from the Top CPU/RAM lists

### 4. **CLI Cache Cleaner**
For a quick cache clean from the terminal:
```bash
python clear_cache_android.py
```

---

## 🖼️ Screenshots
> _Add screenshots of the dashboard here_

---

## 🛠️ Troubleshooting
- **ADB not found:** Make sure ADB is installed and on your PATH
- **Device not detected:** Ensure USB debugging is enabled and device is authorized
- **Dashboard not loading:** Check terminal for errors, try `http://localhost:5050` if port 5000 is busy
- **Permissions:** Some features (like killing apps) may require root on certain devices

---

## 🤝 Contributing
Pull requests and feature suggestions are welcome!
- Fork the repo, create a branch, and submit a PR
- Please include clear commit messages and update documentation as needed

---

## 📄 License
MIT License

---

## 💡 Credits
- Dashboard UI inspired by [Dribbble Call Analytics Dashboard Design](https://dribbble.com/shots/21992874-Call-Analytics-Dashboard-Design)
- Built with Flask, Chart.js, and lots of Android/ADB magic 