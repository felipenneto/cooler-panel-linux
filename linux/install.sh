#!/usr/bin/env bash
set -euo pipefail
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 || $EUID == 0 ]]; then
    echo 'Execute no Bazzite x86_64, como seu usuário, sem sudo.' >&2
    exit 1
fi
if [[ -e /.flatpak-info || -e /run/.containerenv || -e /.dockerenv ]]; then
    echo 'Execute no terminal do sistema, fora de Flatpak/Distrobox/containers.' >&2
    exit 1
fi
/usr/bin/python3 --version
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
data_dir="$HOME/.local/share/cooler-panel"
unit_dir="$HOME/.config/systemd/user"
install -d -- "$data_dir" "$unit_dir"
if systemctl --user is-active --quiet cooler-panel.service; then
    systemctl --user stop cooler-panel.service
fi
install -m 644 -- "$source_dir/cooler_panel.py" "$data_dir/cooler_panel.py"
install -m 644 -- "$source_dir/cooler-panel.service" "$unit_dir/cooler-panel.service"
sudo install -m 644 -- "$source_dir/70-cooler-panel.rules" /etc/udev/rules.d/70-cooler-panel.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw
sudo udevadm settle
systemctl --user daemon-reload
echo 'Instalado. Faça os testes descritos no README antes de ativar a inicialização automática.'
