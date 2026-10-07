# CMS Labs Jupyter runtime

Самостоятельный образ JupyterLab для лабораторных CMS Labs. Он запускается как
обычный Kubernetes workload и не содержит JupyterHub, KubeSpawner, CMS/LTI-клиент
или Kubernetes credentials. Созданием namespace, PVC, Service и маршрута владеет
`clabgate` из `cms-labs-api`.

## Что входит в образ

- официальный `quay.io/jupyter/minimal-notebook` со штатным
  `start-notebook.py`; date tag и multi-arch digest закреплены в Dockerfile;
- библиотеки для Python, RESTCONF/NETCONF, SSH и SNMP;
- `%postman`, `%%ssh` и `%%capture_traffic` из `ipython_startup`;
- compatibility adapter для синхронного `pysnmp ... oneliner.cmdgen` из
  существующей Lab5-1 поверх актуального async API;
- русский language pack, widgets, execution time, resource usage,
  collaboration и `nbgitpuller`;
- proxy identity provider: Clabgate передаёт подтверждённые данные пользователя,
  поэтому collaboration показывает имя пользователя вместо anonymous identity;
- первый раздел Launcher «Задания» на базе `jupyter-app-launcher` со всеми
  найденными `.ipynb`: подпись берётся из первого Markdown-заголовка, а при
  ошибке — из имени файла;
- клиент namespace-local `cms-labs-capture` и ленивый PCAP viewer на
  `ipywidgets`/`tshark`, доступный отдельно через `%view_traffic`;
- воспроизводимый `requirements.lock` с hashes.

Образ слушает порт `8888` и работает пользователем `1000:100`. Домашний каталог
`/home/jovyan` рассчитан на подключение session PVC.

## Локальная сборка

```bash
docker build -t cms-labs-jupyter:local .
docker run --rm -p 8888:8888 cms-labs-jupyter:local
```

Jupyter напечатает URL с одноразовым token. Для проверки режима, используемого
clabgate за авторизующим reverse proxy:

```bash
docker run --rm -p 8888:8888 cms-labs-jupyter:local \
  start-notebook.py \
  --ServerApp.base_url=/clabgate/workspace/demo \
  --IdentityProvider.token=''
```

Сам образ намеренно не реализует OIDC. В production Jupyter доступен только через
workspace proxy `cms-labs-api`, который проверяет scoped session cookie. Публиковать
Service напрямую с отключённым token нельзя.

## Задания в Launcher

До старта JupyterLab hook синхронно обновляет репозиторий через `gitpuller`,
находит в нём все `.ipynb` и создаёт конфигурацию `jupyter-app-launcher`.
Clabgate передаёт образу:

- `CMS_LABS_TASK_URL` — URL Git-репозитория;
- `CMS_LABS_TASK_REF` — ветку или tag;
- `CMS_LABS_TASK_DIR` — каталог клона, по умолчанию `task`.

Скрытые каталоги, включая `.ipynb_checkpoints`, не попадают в список. Если URL
и ref не заданы, сканируется уже существующий `/home/jovyan` — это удобно при
локальном запуске образа с примонтированным workspace.

## Захват трафика

В Kubernetes Clabgate передаёт notebook переменную `CMS_LABS_CAPTURE_URL`, а
контроллер `cms-labs-capture` создаёт API внутри namespace попытки. Kubernetes
credentials образу Jupyter не нужны.

Код ячейки выполняется во время захвата, после чего PCAP скачивается в workspace
и открывается встроенным просмотрщиком:

```python
%%capture_traffic r1:eth1 --filter "icmp" --timeout 15 --save captures/icmp.pcap
run_ssh(nodes["r1"]["host"], ["ping -c 4 10.50.0.2"])
```

Поддерживаются `--packets`, `--max-bytes`, `--snaplen`, `--save` и `--no-view`.
`--filter` является display filter для tshark: текущий Clabernetes capture API
не принимает произвольный kernel BPF. Viewer показывает таблицу пакетов и
лениво загружает protocol tree, hex dump и raw JSON выбранного кадра.

Уже сохранённый файл можно повторно открыть без нового захвата:

```python
%view_traffic captures/icmp.pcap --filter "icmp" --limit 200
```

Вся реализация находится в одном файле `cms_labs_jupyter/traffic_capture.py`.
Его публичные классы можно использовать независимо:

```python
from cms_labs_jupyter.traffic_capture import PcapViewer, TrafficCapture

with TrafficCapture("r1", "eth1", duration=15) as capture:
    generate_traffic()

capture.save("captures/icmp.pcap")
capture.delete()
PcapViewer("captures/icmp.pcap", "icmp").display()
```

Для терминала тот же файл установлен как CLI:

```bash
cms-labs-pcap targets
cms-labs-pcap capture r1:eth1 --timeout 15 --output captures/icmp.pcap
cms-labs-pcap view captures/icmp.pcap --filter icmp
```

## Зависимости

Человек редактирует только `requirements.in`, после чего обновляет lock:

```bash
uv pip compile requirements.in \
  --python-version 3.13 \
  --universal \
  --generate-hashes \
  --exclude-newer 2026-09-23T00:00:00Z \
  --output-file requirements.lock
```

Дата `--exclude-newer` обновляется осознанно при пересборке зависимостей. Это не
даёт свежей публикации в PyPI незаметно изменить уже проверенный image.

## CI

GitHub Actions выполняет repository validation, Hadolint, CodeQL, полную сборку,
`pip check` и smoke test magic-команд. Отдельный workflow собирает multi-arch
образ для `linux/amd64` и `linux/arm64`, добавляет provenance и SBOM и публикует
его в `ghcr.io/<owner>/<repository>`:

- `sha-<commit>` для каждого commit в `main`;
- `<version>` и `<major>.<minor>` для Git tag `v*`;
- `latest` только для Git tag `v*`; push в `main` не изменяет стабильный alias.

Образ автоматически пересобирается каждый понедельник и может быть пересобран
вручную через `workflow_dispatch`. Deploy job в этом репозитории отсутствует:
полный image reference передаётся clabgate через `JUPYTER_IMAGE`.
