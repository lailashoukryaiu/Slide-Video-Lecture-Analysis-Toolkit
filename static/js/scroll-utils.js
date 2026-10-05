export function scrollWithinContainer(container, item, axis = 'vertical') {
    const containerRect = container.getBoundingClientRect();
    const itemRect = item.getBoundingClientRect();
    const options = { behavior: 'smooth' };
    if (axis === 'horizontal' || axis === 'both') {
        if (itemRect.left < containerRect.left || itemRect.right > containerRect.right) {
            options.left = container.scrollLeft + itemRect.left - containerRect.left
                - (container.clientWidth - itemRect.width) / 2;
        }
    }
    if (axis === 'vertical' || axis === 'both') {
        if (itemRect.top < containerRect.top || itemRect.bottom > containerRect.bottom) {
            options.top = container.scrollTop + itemRect.top - containerRect.top
                - (container.clientHeight - itemRect.height) / 2;
        }
    }
    if (options.left === undefined && options.top === undefined) return;
    container.scrollTo(options);
}
