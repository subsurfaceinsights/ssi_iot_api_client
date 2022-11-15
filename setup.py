#!/usr/bin/env python3

"""
Setup file for the SSI IOT API client subpackage
"""

from setuptools import setup


with open('requirements.txt') as f:
    requirements = f.read().splitlines()

setup(
    name='ssi.iot-api-client',
    version='1.0.0',
    author='Doug Johnson',
    author_email='dougvj@gmail.com',
    description='A library for accessing the SSI IOT API',
    long_description=open('README.md').read(),
    packages=['ssi'],
    zip_safe=False,
    install_requires=requirements,
    classifiers=[
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
    ],
)
