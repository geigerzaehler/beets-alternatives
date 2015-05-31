from setuptools import setup

setup(
    name='beets-alternatives',
    version='0.8.3-dev',
    description='beets plugin to manage multiple files',
    long_description=open('README.md').read(),
    author='Thomas Scholtes',
    author_email='thomas-scholtes@gmx.de',
    url='http://www.github.com/geigerzaehler/beets-alternatives',
    license='MIT',
    platforms='ALL',

    test_suite='test',

    packages=['beetsplug'],

    install_requires=[
        'beets>=1.3.13',
        'futures',
    ],

    classifiers=[
        'Topic :: Multimedia :: Sound/Audio',
        'Topic :: Multimedia :: Sound/Audio :: Players :: MP3',
        'License :: OSI Approved :: MIT License',
        'Environment :: Console',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
    ],
)
