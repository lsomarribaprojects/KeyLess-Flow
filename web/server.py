import socket
import threading
from flask import Flask, jsonify, render_template_string
from db.database import TranscriptionDB
from config import DB_PATH

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KeyLess by Sinsajo - Transcripciones</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');
        body { font-family: 'Inter', system-ui, sans-serif; background: #0a0a0a; color: #e5e5e5; }
        .glass { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06); }
        .row-hover:hover { background: rgba(255,255,255,0.05); }
        .text-preview { max-height: 2.6em; overflow: hidden; transition: max-height 0.3s ease; }
        .text-preview.expanded { max-height: 500px; }
        .copied { animation: flash 0.5s ease; }
        @keyframes flash { 0%,100% { background: transparent; } 50% { background: rgba(80,220,120,0.1); } }
        .purple { color: #8c50dc; }
        .orange { color: #ffa028; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 3px; }
    </style>
</head>
<body class="min-h-screen p-6">
    <div class="max-w-4xl mx-auto">
        <!-- Header -->
        <div class="flex items-center justify-between mb-8">
            <div class="flex items-center gap-3">
                <div class="text-2xl font-semibold">
                    <span class="purple">KeyLess</span>
                    <span class="text-white/60 text-lg ml-1">Flow</span>
                </div>
                <span class="text-xs text-white/30 bg-white/5 px-2 py-1 rounded-full" id="count-badge">-</span>
            </div>
            <div class="flex items-center gap-3">
                <input type="text" id="search" placeholder="Buscar..."
                    class="bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-sm text-white/80
                    placeholder-white/30 focus:outline-none focus:border-white/20 w-48">
                <button onclick="loadData()" class="text-white/40 hover:text-white/70 text-sm">Actualizar</button>
            </div>
        </div>

        <!-- Table -->
        <div class="glass rounded-xl overflow-hidden">
            <table class="w-full">
                <thead>
                    <tr class="text-white/40 text-xs uppercase tracking-wider border-b border-white/5">
                        <th class="py-3 px-4 text-left w-36">Hora</th>
                        <th class="py-3 px-4 text-left">Transcripcion</th>
                        <th class="py-3 px-4 text-right w-20">Dur.</th>
                        <th class="py-3 px-4 text-center w-16"></th>
                    </tr>
                </thead>
                <tbody id="tbody"></tbody>
            </table>
            <div id="empty" class="hidden text-center py-12 text-white/20 text-sm">
                No hay transcripciones aun
            </div>
        </div>

        <!-- Footer -->
        <div class="mt-4 text-center text-white/15 text-xs">
            KeyLess by Sinsajo &middot; Ctrl+Alt para grabar &middot; Groq Whisper
        </div>
    </div>

    <script>
        let allData = [];

        async function loadData() {
            const res = await fetch('/api/transcriptions');
            allData = await res.json();
            renderTable(allData);
        }

        function renderTable(data) {
            const tbody = document.getElementById('tbody');
            const empty = document.getElementById('empty');
            const badge = document.getElementById('count-badge');
            badge.textContent = data.length + ' total';

            if (data.length === 0) {
                tbody.innerHTML = '';
                empty.classList.remove('hidden');
                return;
            }
            empty.classList.add('hidden');

            tbody.innerHTML = data.map((t, i) => {
                const date = new Date(t.created_at + 'Z');
                const time = date.toLocaleString('es-MX', {
                    month: 'short', day: 'numeric',
                    hour: '2-digit', minute: '2-digit', second: '2-digit'
                });
                const dur = t.duration_seconds ? t.duration_seconds.toFixed(1) + 's' : '-';
                return `
                <tr class="row-hover border-b border-white/[0.03] cursor-pointer" onclick="toggleExpand(this)">
                    <td class="py-3 px-4 text-white/30 text-xs whitespace-nowrap align-top">${time}</td>
                    <td class="py-3 px-4 text-white/80 text-sm align-top">
                        <div class="text-preview" id="text-${i}">${escapeHtml(t.text)}</div>
                    </td>
                    <td class="py-3 px-4 text-white/20 text-xs text-right align-top">${dur}</td>
                    <td class="py-3 px-4 text-center align-top">
                        <button onclick="event.stopPropagation(); copyText(${i}, this)"
                            class="text-white/20 hover:text-white/60 text-xs px-2 py-1 rounded hover:bg-white/5">
                            Copiar
                        </button>
                    </td>
                </tr>`;
            }).join('');
        }

        function toggleExpand(row) {
            const preview = row.querySelector('.text-preview');
            preview.classList.toggle('expanded');
        }

        function copyText(index, btn) {
            navigator.clipboard.writeText(allData[index].text);
            const row = btn.closest('tr');
            row.classList.add('copied');
            btn.textContent = 'OK';
            setTimeout(() => { btn.textContent = 'Copiar'; row.classList.remove('copied'); }, 1000);
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        // Search
        document.getElementById('search').addEventListener('input', (e) => {
            const q = e.target.value.toLowerCase();
            if (!q) { renderTable(allData); return; }
            renderTable(allData.filter(t => t.text.toLowerCase().includes(q)));
        });

        // Auto-refresh every 5 seconds
        loadData();
        setInterval(loadData, 5000);
    </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/transcriptions")
def get_transcriptions():
    db = TranscriptionDB()
    return jsonify(db.get_recent(limit=200))


def _find_free_port(start: int = 5678, attempts: int = 10) -> int:
    """Find an available port starting from `start`."""
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start  # fallback


def start_web_server(port: int = None) -> int:
    """Start Flask in a daemon thread so it doesn't block the Qt event loop."""
    if port is None:
        port = _find_free_port()
    thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    return port
