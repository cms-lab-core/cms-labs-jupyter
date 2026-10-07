#!/bin/sh

# This file is a Jupyter Docker Stacks startup hook. It is deliberately
# idempotent because clabgate can also invoke it from a postStart hook.
(
    set -eu

    notebook_home="${HOME:-/home/jovyan}"
    settings_dir="${notebook_home}/.jupyter/lab/user-settings/@jupyterlab/translation-extension"
    startup_dir="${notebook_home}/.ipython/profile_default/startup"
    launcher_dir="${JUPYTER_APP_LAUNCHER_PATH:-${notebook_home}/.cms-labs/launcher}"
    task_dir="${CMS_LABS_TASK_DIR:-task}"
    task_root="${notebook_home}/${task_dir}"
    startup_key=$(printf '%s' "${notebook_home}" | cksum)
    startup_key=${startup_key%% *}
    run_marker="/tmp/cms-labs-notebook-startup-${startup_key}.done"
    run_lock="/tmp/cms-labs-notebook-startup-${startup_key}.lock"

    if [ -f "${run_marker}" ]; then
        exit 0
    fi
    if ! mkdir "${run_lock}" 2>/dev/null; then
        while [ ! -f "${run_marker}" ] && [ -d "${run_lock}" ]; do sleep 1; done
        exit 0
    fi
    trap 'rmdir "${run_lock}" 2>/dev/null || true' EXIT

    case "${task_dir}" in
        ""|.*|*/*|*\\*)
            printf 'Invalid CMS_LABS_TASK_DIR: %s\n' "${task_dir}" >&2
            exit 1
            ;;
    esac

    mkdir -p "${settings_dir}" "${startup_dir}"
    printf '%s\n' '{"locale": "ru_RU"}' > \
        "${settings_dir}/plugin.jupyterlab-settings"

    if [ -d /opt/cms-labs/ipython_startup ]; then
        cp -R /opt/cms-labs/ipython_startup/. "${startup_dir}/"
    fi

    if [ -n "${CMS_LABS_TASK_URL:-}" ] && [ -n "${CMS_LABS_TASK_REF:-}" ]; then
        cd "${notebook_home}"
        gitpuller "${CMS_LABS_TASK_URL}" "${CMS_LABS_TASK_REF}" "${task_dir}"
    else
        task_root="${notebook_home}"
    fi

    python -m cms_labs_jupyter.tasks_launcher \
        "${task_root}" "${launcher_dir}/jp_app_launcher_tasks.yaml" \
        --workspace-root "${notebook_home}"
    touch "${run_marker}"
)
