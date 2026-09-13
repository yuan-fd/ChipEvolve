"""Negative fixture for G5: app opens the kernel runtime database."""
import sqlite3

def load(path):
    return sqlite3.connect("runtime.db")
