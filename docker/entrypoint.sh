#!/usr/bin/env bash
# Точка входа «толстого» образа.
#
# Если задан VISTEST_VNC_PORT — поднимаем виртуальный дисплей и окно записи
# (noVNC). Тогда интерактивная запись мышью работает и в контейнере на ВМ без
# экрана: браузер открывается на дисплее :99, а команда водит мышью из своего
# браузера по этому порту. Без переменной — просто сервис (pytest и снятие
# эталонов по URL идут headless и дисплея не требуют).
set -euo pipefail

if [ -n "${VISTEST_VNC_PORT:-}" ]; then
  echo "[vistest] окно записи включено (noVNC на :${VISTEST_VNC_PORT})"
  export DISPLAY="${DISPLAY:-:99}"
  SCREEN="${VISTEST_SCREEN:-1600x900x24}"

  rm -f /tmp/.X99-lock 2>/dev/null || true
  Xvfb :99 -screen 0 "${SCREEN}" -nolisten tcp &
  # ждём, пока дисплей поднимется
  for _ in $(seq 1 40); do
    [ -e /tmp/.X11-unix/X99 ] && break
    sleep 0.2
  done
  # оконный менеджер нужен: без него --start-maximized не срабатывает и окно
  # записи открывается крохотным в углу вместо целого экрана.
  fluxbox >/dev/null 2>&1 &
  x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -quiet -bg >/dev/null 2>&1
  websockify --web=/usr/share/novnc "${VISTEST_VNC_PORT}" localhost:5900 \
    >/dev/null 2>&1 &
fi

# --proxy-headers обязателен. Без него uvicorn видит схему запроса как http
# даже тогда, когда снаружи TLS терминирует nginx или Caddy, — и кука сессии
# никогда не получает флаг Secure. Если прокси один и известен, сузьте список
# доверенных источников переменной VISTEST_FORWARDED_ALLOW_IPS.
exec uvicorn vistest.api.main:app --host 0.0.0.0 --port 8420 \
  --proxy-headers --forwarded-allow-ips="${VISTEST_FORWARDED_ALLOW_IPS:-*}"
