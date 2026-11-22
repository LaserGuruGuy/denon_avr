"""Services: the receiver's subsystems, orchestrated over ports (see ports.py).

A service never imports a concrete transport; the facade (device.py) is the
composition root that wires transports into the ports. Services hold the
behaviour (what to query, when, in what order); the wire grammar is in protocol/.
"""
