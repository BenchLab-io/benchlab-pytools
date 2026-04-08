#!/usr/bin/env python3
"""
Test script for Xeneon Dashboard
"""

import requests
import time

def test_endpoint(url, description):
    """Test a single endpoint and print results"""
    try:
        response = requests.get(url, timeout=5)
        status = "✓ PASS" if response.status_code == 200 else f"✗ FAIL ({response.status_code})"
        print(f"{status} - {description}: {url}")
        if response.status_code == 200:
            print(f"    Content-Type: {response.headers.get('content-type', 'unknown')}")
            if 'text/html' in response.headers.get('content-type', ''):
                print(f"    Content length: {len(response.text)} bytes")
        return response.status_code == 200
    except Exception as e:
        print(f"✗ ERROR - {description}: {url}")
        print(f"    Error: {e}")
        return False

def main():
    base_url = "http://localhost:8001"
    
    print("Testing Xeneon Dashboard Endpoints")
    print("=" * 50)
    
    # Test basic endpoints
    endpoints = [
        (f"{base_url}/", "Root redirect"),
        (f"{base_url}/xeneon", "Dashboard iframe"),
        (f"{base_url}/xeneon/dashboard", "Dashboard main page"),
        (f"{base_url}/health", "Health check"),
        (f"{base_url}/config", "Dashboard config"),
        (f"{base_url}/devices", "Device list"),
        (f"{base_url}/xeneon/static/css/dashboard.css", "CSS file"),
        (f"{base_url}/xeneon/static/js/dashboard.js", "JavaScript file"),
    ]
    
    results = []
    for url, description in endpoints:
        success = test_endpoint(url, description)
        results.append(success)
        time.sleep(0.1)  # Small delay between requests
    
    print("\n" + "=" * 50)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} endpoints working")
    
    if passed == total:
        print("🎉 All endpoints are working correctly!")
    else:
        print(f"⚠️  {total - passed} endpoints failed")

if __name__ == "__main__":
    main()