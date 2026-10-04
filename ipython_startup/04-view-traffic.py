"""Register the interactive viewer for saved PCAP files."""

from cms_labs_jupyter.traffic_capture import register_view_magic


register_view_magic(get_ipython())
