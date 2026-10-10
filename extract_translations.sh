#!/usr/bin/env bash

echo
echo "Checking Python"
echo

if ! python3 --version 2>/dev/null; then
    echo
    echo "Python NOT installed!"
    exit 1
fi

echo
read -r -p "Activate Python VENV? A/N: " answer

if [[ "$answer" == [Aa] ]]; then
    echo
    echo "Activating VENV"

    if [[ ! -f .venv/bin/activate ]]; then
        echo "VENV not found: .venv/bin/activate"
        exit 1
    fi

    source .venv/bin/activate
fi

echo
if ! pybabel extract \
    -F babel.cfg \
    -o messages.pot \
    .; then
    echo "Translation extraction failed."
    exit 1
fi

echo
echo "Finished - use Poedit"
echo
read -r -n 1 -s -p "Press any key ..."
echo
