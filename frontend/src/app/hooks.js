import { useDispatch, useSelector } from 'react-redux';

// Typed wrappers — keeps components free of store import
export const useAppDispatch = () => useDispatch();
export const useAppSelector = (selector) => useSelector(selector);
