<?php

namespace App\Http\Controllers\Api;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use App\Http\Controllers\Controller;

class JobController extends Controller
{
    /**
     * クローラーコンテナのベース URL
     * 環境変数 CRAWLER_URL で上書き可能
     */
    private function crawlerUrl(): string
    {
        return rtrim(env('CRAWLER_URL', 'http://crawler:8001'), '/');
    }

    /**
     * ジョブ状態を取得する（クローラーコンテナに転送）
     */
    public function status()
    {
        try {
            $response = Http::timeout(5)->get($this->crawlerUrl() . '/api/status');
            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['running' => false, 'count' => 0, 'error' => $e->getMessage()], 503);
        }
    }

    /**
     * クロールを開始する（クローラーコンテナに転送）
     */
    public function run(Request $request)
    {
        $start = $request->query('start');

        try {
            $url = $this->crawlerUrl() . '/api/run';
            if ($start !== null) {
                $url .= '?start=' . urlencode($start);
            }
            $response = Http::timeout(10)->post($url);
            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['ok' => false, 'error' => $e->getMessage()], 503);
        }
    }

    /**
     * クロールを停止する（クローラーコンテナに転送）
     */
    public function stop()
    {
        try {
            $response = Http::timeout(5)->post($this->crawlerUrl() . '/api/stop');
            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['ok' => false, 'error' => $e->getMessage()], 503);
        }
    }
}
