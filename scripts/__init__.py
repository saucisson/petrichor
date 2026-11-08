"""Driver commands of the template: one package, one CLI (ADR-0021).

Every driver lives in this package and runs as one subcommand of
``python -m scripts`` - and, one-to-one, as one ``make`` target, which
stays THE agent surface (ADR-0021). Each module is one driver domain;
``_shared`` carries the plumbing shared by all of them.
"""
