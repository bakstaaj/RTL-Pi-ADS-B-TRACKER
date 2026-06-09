# Packaged readsb binary

This readsb binary is cross-compiled for Raspberry Pi OS / Debian Trixie ARM64.

Source repo:

https://github.com/wiedehopf/readsb.git

Requested source ref:

dev

Resolved commit:

0bfd0473d0d6c9bd46dcc7091a323b945b165d15

Build flags:

make CC=aarch64-linux-gnu-gcc RTLSDR=yes OPTIMIZE="-O3"

Installed on Pi as:

/opt/rtl-pi-adsb-tracker/bin/readsb

The Debian /usr/bin/readsb binary is not used by this application.
