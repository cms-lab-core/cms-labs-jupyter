# CMS Labs Jupyter runtime

Самостоятельный образ JupyterLab для лабораторных CMS Labs. Он запускается как
обычный Kubernetes workload и не содержит JupyterHub, KubeSpawner, CMS/LTI-клиент
или Kubernetes credentials. Созданием namespace, PVC, Service и маршрута владеет
`clabgate` из `cms-labs-api`.

## Что входит в образ

- официальный `quay.io/jupyter/minimal-notebook` со штатным
  `start-notebook.py`; date tag и multi-arch digest закреплены в Dockerfile;
- библиотеки для Python, RESTCONF/NETCONF, SSH и SNMP;
- `%postman` и `%%ssh` из `ipython_startup`;
- compatibility adapter для синхронного `pysnmp ... oneliner.cmdgen` из
  существующей Lab5-1 поверх актуального async API;
- русский language pack, widgets, execution time, resource usage,
  collaboration и `nbgitpuller`;
- proxy identity provider: Clabgate передаёт подтверждённые данные пользователя,
  поэтому collaboration показывает имя пользователя вместо anonymous identity;
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
- `latest` для `main`.

Образ автоматически пересобирается каждый понедельник и может быть пересобран
вручную через `workflow_dispatch`. Deploy job в этом репозитории отсутствует:
полный image reference передаётся clabgate через `JUPYTER_IMAGE`.
