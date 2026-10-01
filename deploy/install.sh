#!/usr/bin/env bash
# Install autoshorts on an Ubuntu/Debian server (or desktop).
#
#   sudo git clone https://github.com/georgemallousis-design/cla.git /opt/autoshorts
#   sudo /opt/autoshorts/deploy/install.sh
#
# What it does (safe to run again, e.g. after `git pull`):
#   1. apt packages: ffmpeg (with libass/libx264), espeak-ng, fonts, python3-venv
#   2. a system user "autoshorts" that owns the repo folder and runs the videos
#   3. a virtualenv in <repo>/.venv with autoshorts and the YouTube upload extras
#   4. `autoshorts init`: config.yaml, .env and topics.txt in <repo> (never overwritten)
#   5. /usr/local/bin/autoshorts: runs autoshorts as that user inside <repo>
#   6. systemd units autoshorts.service + autoshorts.timer (installed, NOT enabled)
#
# Options:
#   --user NAME     run videos as this user instead of "autoshorts" (created if missing)
#   --no-systemd    skip step 6
#   --enable-timer  also enable the timer (3 videos a day) right away
set -euo pipefail

RUN_USER="autoshorts"
INSTALL_UNITS=1
ENABLE_TIMER=0

usage() {
    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user)
            [[ $# -ge 2 ]] || { echo "error: --user needs a name" >&2; exit 2; }
            RUN_USER="$2"
            shift 2
            ;;
        --no-systemd) INSTALL_UNITS=0; shift ;;
        --enable-timer) ENABLE_TIMER=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown option '$1' (see --help)" >&2; exit 2 ;;
    esac
done

if [[ ${EUID} -ne 0 ]]; then
    echo "error: run this with sudo (it installs system packages)" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ ! -f "${APP_DIR}/pyproject.toml" ]]; then
    echo "error: ${APP_DIR} does not look like the autoshorts repository" >&2
    exit 1
fi

step() { printf '\n==> %s\n' "$*"; }

# --------------------------------------------------------------------------- 1. packages
step "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    ffmpeg espeak-ng fonts-dejavu-core fonts-liberation fontconfig \
    python3 python3-venv python3-pip ca-certificates git sudo

python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    sys.exit(f"error: autoshorts needs Python 3.10 or newer; this system has {sys.version.split()[0]}. "
             "Use Ubuntu 22.04+/Debian 12+, or install a newer python3 first.")
PY

FFMPEG_FILTERS="$(ffmpeg -hide_banner -filters 2>/dev/null || true)"
if ! grep -qw subtitles <<<"${FFMPEG_FILTERS}"; then
    echo "warning: this ffmpeg has no 'subtitles' filter (libass); captions will not work" >&2
fi

# --------------------------------------------------------------------------- 2. user
step "Setting up user '${RUN_USER}'"
if ! id -u "${RUN_USER}" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/var/lib/${RUN_USER}" \
        --shell /usr/sbin/nologin "${RUN_USER}"
    echo "created system user ${RUN_USER}"
fi
RUN_GROUP="$(id -gn "${RUN_USER}")"
RUN_HOME="$(getent passwd "${RUN_USER}" | cut -d: -f6)"
chown -R "${RUN_USER}:${RUN_GROUP}" "${APP_DIR}"

as_user() {
    runuser -u "${RUN_USER}" -- env HOME="${RUN_HOME}" "$@"
}

# --------------------------------------------------------------------------- 3. virtualenv
step "Creating the virtualenv in ${APP_DIR}/.venv"
cd "${APP_DIR}"
if [[ ! -x .venv/bin/python ]]; then
    as_user python3 -m venv .venv
fi
as_user .venv/bin/python -m pip install --upgrade pip
as_user .venv/bin/python -m pip install -e "${APP_DIR}[youtube]"

# --------------------------------------------------------------------------- 4. config files
step "Creating config.yaml, .env and topics.txt (existing files are kept)"
as_user .venv/bin/autoshorts init
chmod 600 "${APP_DIR}/.env" 2>/dev/null || true

# --------------------------------------------------------------------------- 5. wrapper
step "Installing /usr/local/bin/autoshorts"
cat > /usr/local/bin/autoshorts <<EOF
#!/bin/sh
# Installed by ${APP_DIR}/deploy/install.sh: runs autoshorts as ${RUN_USER} in ${APP_DIR}.
cd "${APP_DIR}" || exit 1
if [ "\$(id -un)" = "${RUN_USER}" ]; then
    exec "${APP_DIR}/.venv/bin/autoshorts" "\$@"
fi
exec sudo -u "${RUN_USER}" -H "${APP_DIR}/.venv/bin/autoshorts" "\$@"
EOF
chmod 755 /usr/local/bin/autoshorts

# --------------------------------------------------------------------------- 6. systemd
if [[ ${INSTALL_UNITS} -eq 1 ]] && command -v systemctl >/dev/null 2>&1; then
    step "Installing systemd units (autoshorts.service, autoshorts.timer)"
    for unit in autoshorts.service autoshorts.timer; do
        sed -e "s#/opt/autoshorts#${APP_DIR}#g" \
            -e "s#^User=autoshorts\$#User=${RUN_USER}#" \
            -e "s#^Group=autoshorts\$#Group=${RUN_GROUP}#" \
            "${SCRIPT_DIR}/${unit}" > "/etc/systemd/system/${unit}"
    done
    systemctl daemon-reload
    if [[ ${ENABLE_TIMER} -eq 1 ]]; then
        systemctl enable --now autoshorts.timer
        echo "timer enabled: $(systemctl list-timers autoshorts.timer --no-legend | head -n 1)"
    fi
fi

cat <<EOF

Done. autoshorts is installed in ${APP_DIR} and runs as '${RUN_USER}'.

Next steps:
  1. Add your free API keys:      sudo nano ${APP_DIR}/.env
     Tune settings (optional):    sudo nano ${APP_DIR}/config.yaml
  2. Check everything:            autoshorts doctor
  3. Make a test video:           autoshorts make
     (videos land in ${APP_DIR}/output/)
  4. YouTube upload: authorise on your PC (autoshorts auth youtube), then copy
     secrets/client_secret.json and secrets/youtube_token.json to ${APP_DIR}/secrets/
     and run:  sudo chown -R ${RUN_USER}:${RUN_GROUP} ${APP_DIR}/secrets
  5. Start the schedule (3 videos a day):
       sudo systemctl start autoshorts.service       # one run now, log: journalctl -u autoshorts -e
       sudo systemctl enable --now autoshorts.timer

Update later:  sudo -u ${RUN_USER} git -C ${APP_DIR} pull && sudo ${APP_DIR}/deploy/install.sh
EOF
