export function scrollWithinContainer(container, item, axis = 'vertical') {
    const containerRect = container.getBoundingClientRect();
    const itemRect = item.getBoundingClientRect();
    if (axis === 'horizontal') {
        if (itemRect.left >= containerRect.left && itemRect.right <= containerRect.right) return;
        container.scrollTo({
            left: container.scrollLeft + itemRect.left - containerRect.left
                - (container.clientWidth - itemRect.width) / 2,
            behavior: 'smooth',
        });
    } else {
        if (itemRect.top >= containerRect.top && itemRect.bottom <= containerRect.bottom) return;
        container.scrollTo({
            top: container.scrollTop + itemRect.top - containerRect.top
                - (container.clientHeight - itemRect.height) / 2,
            behavior: 'smooth',
        });
    }
}
