import os
import requests
from bs4 import BeautifulSoup
import time

def fetch_and_save(url, filename):
    print(f"Fetching {url}...")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(response.text)
        return response.text
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def main():
    temp_dir = 'temp'
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)
    
    projects_url = 'https://mostaql.com/projects'
    projects_html = fetch_and_save(projects_url, os.path.join(temp_dir, 'projects_list.html'))
    
    if not projects_html:
        return

    soup = BeautifulSoup(projects_html, 'html.parser')
    # Finding project links. Based on common Mostaql structure, they are often in h3 or similar with project title
    links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/project/' in href and 'create?' not in href and href not in links:
            links.append(href)
            if len(links) >= 20:
                break
    
    print(f"Found {len(links)} project links.")
    
    for i, link in enumerate(links):
        if not link.startswith('http'):
            link = 'https://mostaql.com' + link
        filename = os.path.join(temp_dir, f'project_{i+1}.html')
        fetch_and_save(link, filename)
        time.sleep(1) # Be nice to the server

if __name__ == '__main__':
    main()
