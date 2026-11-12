# NBP interest-rate table fixture

`stopy_procentowe.xml` follows the structure of
`https://static.nbp.pl/dane/stopy/stopy_procentowe.xml`. NBP was not reachable
from the session that wrote it, so it is not a raw capture: the root element,
the `stoproc` table and the `ref` row (reference rate 3,75, effective
2026-03-05) follow the sample the owner supplied; the other rows (lombard,
deposit, rediscount, discount) are illustrative and are not read by the
collector. Replace it with a raw capture when the endpoint is reachable.
