# -*- coding: utf-8 -*-
"""Collection: Forex Factory's public JSON feed to normalised rows.

Deliberately empty of imports. The requests-only ``forexfactory`` collector
can be imported directly; keeping this package free of re-exports also leaves
the legacy browser-based ``myfxbook`` module optional.

Import the module you need:

    from market_data_hub.econ_calendar.collect import forexfactory, timezones
    from market_data_hub.econ_calendar.collect.consolidate import raccogli
"""
