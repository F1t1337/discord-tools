#!/usr/bin/env python3
"""
Диагностика Cloudflare Tunnel
"""
import requests
import re

print("🔍 Диагностика Cloudflare Tunnel...\n")

# Проверяем разные порты metrics
ports = [43867, 60000, 60123,20242]  # Стандартные порты Cloudflare

for port in ports:
    metrics_url = f"http://127.0.0.1:{port}/metrics"
    print(f"Проверка порта {port}...")
    
    try:
        response = requests.get(metrics_url, timeout=2)
        
        if response.status_code == 200:
            print(f"✅ Cloudflare metrics доступны на порту {port}!")
            print(f"📊 Размер ответа: {len(response.text)} байт")
            print()
            
            # Парсим URL
            text = response.text
            
            # Метод 1: userHostname
            pattern1 = r'userHostname="(https://[^"]+)"'
            matches1 = re.findall(pattern1, text)
            
            if matches1:
                print(f"✅ Найден URL (метод 1 - userHostname):")
                for url in matches1:
                    print(f"   🔗 {url}")
            
            # Метод 2: прямой поиск trycloudflare.com
            pattern2 = r'(https://[a-z0-9\-]+\.trycloudflare\.com)'
            matches2 = re.findall(pattern2, text)
            
            if matches2:
                print(f"✅ Найден URL (метод 2 - regex):")
                for url in set(matches2):  # Убираем дубликаты
                    print(f"   🔗 {url}")
            
            # Показываем первые 50 строк metrics
            print()
            print("📄 Первые строки metrics:")
            print("=" * 50)
            lines = text.split('\n')[:50]
            for line in lines:
                if 'cloudflared' in line.lower() or 'tunnel' in line.lower() or 'https' in line.lower():
                    print(f"   {line}")
            print("=" * 50)
            print()
            
            # Если нашли URL - выходим
            if matches1 or matches2:
                print()
                print("✅ РЕШЕНИЕ:")
                print(f"   Используйте порт {port} в cloudflare_helper.py")
                print(f"   metrics_url = 'http://127.0.0.1:{port}/metrics'")
                break
                
    except requests.exceptions.RequestException as e:
        print(f"❌ Порт {port} недоступен: {e}")
    
    print()

# Проверяем альтернативный метод - через quicktunnel endpoint
print()
print("Проверка альтернативных endpoint'ов...")
print()

alternative_endpoints = [
    "http://127.0.0.1:43867/quicktunnel",
    "http://127.0.0.1:60000/quicktunnel",
]

for endpoint in alternative_endpoints:
    try:
        response = requests.get(endpoint, timeout=2)
        if response.status_code == 200:
            print(f"✅ Доступен: {endpoint}")
            print(f"📊 Ответ: {response.text[:500]}")
            print()
    except:
        pass

print()
print("=" * 60)
print("📋 ИТОГО:")
print("=" * 60)
print()
print("Если URL найден выше - обновите cloudflare_helper.py")
print("Если URL НЕ найден - проверьте что Cloudflare Tunnel запущен:")
print()
print("   cloudflared tunnel --url http://localhost:5000")
print()
print("Также попробуйте проверить вывод терминала где запущен cloudflared")
print("Там должна быть строка с URL!")
print()