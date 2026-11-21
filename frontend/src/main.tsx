import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App as AntApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import dayjs from 'dayjs';
import isoWeek from 'dayjs/plugin/isoWeek';
import 'dayjs/locale/zh-cn';
import 'antd/dist/reset.css';
import './styles/app.css';
import App from './App';

dayjs.extend(isoWeek);
dayjs.locale('zh-cn');
const client = new QueryClient({ defaultOptions: { queries: {
  retry: (failures, error: any) => failures < 2 && (!error.response || error.response.status >= 500),
  retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 10000),
  staleTime: 5000, refetchIntervalInBackground: false,
} } });
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><QueryClientProvider client={client}>
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#245ba6', borderRadius: 8, colorBgLayout: '#f4f6fa', colorText: '#29374e', fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif' }, components: { Menu: { itemBorderRadius: 7, itemMarginInline: 12, itemHeight: 42 }, Card: { headerFontSize: 15 } } }}>
      <AntApp><BrowserRouter><App /></BrowserRouter></AntApp>
    </ConfigProvider>
  </QueryClientProvider></React.StrictMode>,
);
