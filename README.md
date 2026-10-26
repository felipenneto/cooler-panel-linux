# Cooler Panel para Linux / Bazzite — prévia 0.1.0

Versão independente para o **visor USB AA88:8666**, voltada ao Ryzen 7 5700X com Radeon RX 9060 XT. Usa Python 3.9+ e sua biblioteca padrão, os sensores `k10temp`/`amdgpu` do kernel e `hidraw`. Não requer MangoHud, RTSS, Wine, pip ou um módulo de kernel próprio. Pode ser usada junto com o MangoHud, inclusive sem jogo aberto.

**Ainda não validada em um computador Bazzite nem no visor físico pelo Linux.** Os testes automatizados usam dados simulados; o protocolo foi confirmado anteriormente apenas no Windows, em um Husky Glacier HWT700PT. Este outro computador precisa ter um visor com o mesmo protocolo: CPU/GPU semelhantes não comprovam compatibilidade do cooler. Outros identificadores são recusados; o identificador correto também não garante uma revisão compatível.

Esta primeira versão roda em segundo plano, sem janela ou ícone de bandeja. Atualiza temperaturas e utilização de CPU/GPU a cada segundo. Não controla bomba, ventoinhas ou ARGB; demais campos são zero.

## Instalar no Bazzite

Extraia o pacote numa pasta e abra o terminal **do próprio Bazzite**, fora do Distrobox/Flatpak. Entre na pasta `linux` e execute:

```bash
bash install.sh
```

O instalador pede a senha somente para instalar uma regra de acesso USB específica em `/etc/udev/rules.d/70-cooler-panel.rules` e recarregar as regras. O programa e o serviço ficam em `~/.local/share/cooler-panel/` e `~/.config/systemd/user/`. Não instala pacotes nem altera `/usr`. Requer `/usr/bin/python3` disponível no sistema; o instalador verifica isso antes de copiar arquivos.

A regra libera o visor para o usuário da sessão local ativa. Não execute o aplicativo com sudo. Em caso de permissão negada, reinicie o computador e tente novamente na sessão local. SSH/sessões sem acesso ao assento local podem não receber essa permissão.

O instalador não inicia o programa automaticamente. Se estiver atualizando uma instalação em execução, ele para o serviço antes de substituir os arquivos; inicie-o novamente após os testes.

## Validar antes de ativar

Execute os comandos abaixo, um de cada vez. Pare se algum apresentar erro:

```bash
python3 ~/.local/share/cooler-panel/cooler_panel.py --probe
python3 ~/.local/share/cooler-panel/cooler_panel.py --sensors
python3 ~/.local/share/cooler-panel/cooler_panel.py --send-test
```

1. `--probe` identifica **uma única** interface USB AA88:8666 e valida o descritor. Não envia valores ao visor, mas precisa de permissão de abertura.
2. `--sensors` mostra temperaturas e utilização, junto aos caminhos dos sensores. Compare as temperaturas com o MangoHud. A CPU prefere `Tdie`, depois `Tctl`; a GPU usa `temp1_input` (edge), que pode diferir de junction/hotspot. A utilização pode divergir por causa do intervalo de amostragem.
3. `--send-test` envia **CPU 42 °C e GPU 37 °C simulados** uma vez. Confirme os números no visor físico. Se houver erro de permissão, confira a regra e a sessão local; não use permissões globais como `chmod 666`.

Se o visor estiver correto, teste as leituras reais em primeiro plano:

```bash
python3 ~/.local/share/cooler-panel/cooler_panel.py --run
```

Deixe por alguns minutos, com e sem jogo aberto. Encerre com Ctrl+C. A leitura de GPU requer `amdgpu` e uma GPU AMD identificada; a de CPU requer um `k10temp`. O programa recusa sensores ausentes ou múltiplos dispositivos do mesmo tipo em vez de escolher arbitrariamente. Se `gpu_busy_percent` não existir, a utilização da GPU será zero; temperaturas continuam obrigatórias.

## Iniciar no login

Após confirmar os valores no visor:

```bash
systemctl --user enable --now cooler-panel.service
```

Inicia com a sessão do usuário, sem depender de jogo ou MangoHud. A execução no modo jogo do Bazzite precisa ser confirmada nesse computador, especialmente a concessão de acesso ao USB. Não é um serviço que inicia antes do login.

Consultar estado e erros:

```bash
systemctl --user status cooler-panel.service
journalctl --user -u cooler-panel.service -b -n 50 --no-pager
```

Antes de enviar outro teste manual:

```bash
systemctl --user stop cooler-panel.service
```

Após o teste:

```bash
systemctl --user start cooler-panel.service
```

Desconexões e falhas de sensores suspendem os envios e provocam nova tentativa após cinco segundos. O visor pode permanecer com o último valor: não serve como proteção térmica. O programa valida o descritor HID e bloqueia relatórios numerados ou com saída diferente dos 10 bytes de dados + ID zero confirmados. Não tenta adaptar protocolos desconhecidos.

## Remover

```bash
systemctl --user disable --now cooler-panel.service
rm -f -- ~/.config/systemd/user/cooler-panel.service
rm -f -- ~/.local/share/cooler-panel/cooler_panel.py
rmdir -- ~/.local/share/cooler-panel
systemctl --user daemon-reload
sudo rm -f -- /etc/udev/rules.d/70-cooler-panel.rules
sudo udevadm control --reload-rules
```

Reinicie para encerrar eventuais permissões USB já concedidas. Não remova o Python nem os drivers do sistema.

## Testes locais sem hardware

Na pasta `linux`:

```bash
python3 -m unittest -v
```

Cobrem o pacote USB, o descritor, a validação do dispositivo aberto, envio incompleto, escolha de sensores, erros de leitura e cálculo de utilização. Não comprovam acesso USB, permissões, inicialização ou sensores reais no Bazzite.

Referências: [hidraw](https://docs.kernel.org/hid/hidraw.html), [k10temp](https://docs.kernel.org/hwmon/k10temp.html), [sensores amdgpu](https://docs.kernel.org/gpu/amdgpu/thermal.html), [software no Bazzite](https://docs.bazzite.gg/Installing_and_Managing_Software/software-intro/).

Licença MIT, conforme `LICENSE` na raiz do projeto.
