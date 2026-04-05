from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

with open("requirements.txt") as f:
    requirements = [
        line.strip() for line in f
        if line.strip() and not line.startswith("#")
    ]

setup(
    name="apk2ipa",
    version="0.1.0",
    author="apk2ipa contributors",
    description="APK to IPA (and vice-versa) translation pipeline",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    include_package_data=True,
    install_requires=requirements,
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "apk2ipa=apk2ipa.cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Build Tools",
        "Topic :: Utilities",
    ],
)
