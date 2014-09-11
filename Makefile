todo:
	@ack-grep "(TODO|FIXME) .*" -o --no-group

flake8:
	flake8 beetsplug test setup.py
