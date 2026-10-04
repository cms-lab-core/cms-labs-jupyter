"""Register CMS Labs traffic capture magic in every kernel."""

from cms_labs_jupyter.traffic_capture import register_capture_magic


register_capture_magic(get_ipython())
