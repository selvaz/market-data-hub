# -*- coding: utf-8 -*-
"""Collection: MyFXBook, from the page to a normalised row.

Deliberately empty of imports. ``myfxbook`` needs selenium and a real
browser, which are an optional extra (`pip install market-data-hub[calendar]`)
and are absent on the 3.9 leg of CI. Re-exporting it here would make
``import market_data_hub.econ_calendar.collect`` fail wherever the extra is
not installed, and take the parts that need nothing -- `matching`,
`timezones` -- down with it.

Import the module you need:

    from market_data_hub.econ_calendar.collect import myfxbook, timezones
    from market_data_hub.econ_calendar.collect.consolidate import raccogli
"""
