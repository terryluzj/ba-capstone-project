# Automatically created by: shub deploy

from setuptools import setup, find_packages

setup(
    name         = 'crawler',
    version      = '1.0',
    packages     = find_packages(),
    package_data = {'crawler': ['data/*.csv', 'data/*.json']},
    entry_points = {'scrapy': ['settings = crawler.settings']},
    zip_safe = False,
)

