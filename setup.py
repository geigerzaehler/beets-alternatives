from setuptools import setup

setup(
    name='beets-alternatives',
    version='0.11.0-dev0',
    description='beets plugin to manage multiple files',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    author='Thomas Scholtes',
    author_email='thomas-scholtes@gmx.de',
    url='http://www.github.com/geigerzaehler/beets-alternatives',
    license='MIT',
    platforms='ALL',

    test_suite='test',

    packages=['beetsplug'],

    install_requires=[
        'beets>=1.6.0',
        'six',
    ],

    classifiers=[
        'Topic :: Multimedia :: Sound/Audio',
        'Topic :: Multimedia :: Sound/Audio :: Players :: MP3',
        'License :: OSI Approved :: MIT License',
        'Environment :: Console',
        'Programming Language :: Python',
    ],
)
