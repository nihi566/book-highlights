<?php

namespace App\Http\Controllers\Api;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use App\Http\Controllers\Controller;

class BookController extends Controller
{
    /**
     * 書籍一覧と最新価格を返す
     */
    public function index()
    {
        $query = <<<SQL
        WITH latest_prices AS (
            SELECT p1.paid_asin, p1.sell_price, p1.point_value, p1.actual_price, p1.campaign_text, p1.timestamp, p1.is_unlimited
            FROM price_history p1
            INNER JOIN (
                SELECT paid_asin, MAX(timestamp) as max_ts
                FROM price_history
                GROUP BY paid_asin
            ) p2 ON p1.paid_asin = p2.paid_asin AND p1.timestamp = p2.max_ts
        )
        SELECT
            m.title,
            l.paid_asin as asin,
            l.sell_price,
            l.point_value,
            l.actual_price,
            l.campaign_text,
            l.timestamp,
            l.is_unlimited,
            COALESCE(m.is_purchased, 0) as is_purchased,
            COALESCE(m.is_wanted,   0) as is_wanted
        FROM book_mappings m
        JOIN latest_prices l ON m.paid_asin = l.paid_asin
        ORDER BY l.actual_price ASC
        SQL;

        $books = DB::select($query);

        // stdClass を配列に変換
        return response()->json(array_map(fn($row) => (array) $row, $books));
    }

    /**
     * 指定 ASIN の価格履歴を返す
     */
    public function history(string $asin)
    {
        $query = <<<SQL
        SELECT sell_price, point_value, actual_price, campaign_text, timestamp, is_unlimited
        FROM price_history
        WHERE paid_asin = ?
        ORDER BY timestamp ASC
        SQL;

        $history = DB::select($query, [$asin]);

        return response()->json(array_map(fn($row) => (array) $row, $history));
    }

    /**
     * 「欲しい」ステータスを更新する
     */
    public function want(Request $request)
    {
        $asin   = $request->query('asin');
        $status = (int) $request->query('status', 1);

        if (! $asin) {
            return response()->json(['error' => 'asin パラメータが必要です。'], 400);
        }

        $count = DB::table('book_mappings')
            ->where('paid_asin', $asin)
            ->update(['is_wanted' => $status]);

        return response()->json([
            'ok'        => $count > 0,
            'asin'      => $asin,
            'is_wanted' => $status,
        ]);
    }

    /**
     * 購入ステータスを更新する
     */
    public function purchase(Request $request)
    {
        $asin   = $request->query('asin');
        $status = (int) $request->query('status', 1);

        if (! $asin) {
            return response()->json(['error' => 'asin パラメータが必要です。'], 400);
        }

        $count = DB::table('book_mappings')
            ->where('paid_asin', $asin)
            ->update(['is_purchased' => $status]);

        return response()->json([
            'ok'           => $count > 0,
            'asin'         => $asin,
            'is_purchased' => $status,
        ]);
    }
}
