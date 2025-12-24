function toggleMenu() {
    const menu = document.getElementById('mobile-menu');
    if (menu.style.display === 'flex') {
        menu.style.display = 'none';
    } else {
        menu.style.display = 'flex';
    }
}

// Sidebar beim Scrollen schließen
document.querySelectorAll('.mobile-nav a').forEach(link => {
    link.addEventListener('click', () => {
        document.getElementById('mobile-menu').style.display = 'none';
    });
});