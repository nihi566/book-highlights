<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class PriceHistory extends Model
{
    protected $table      = 'price_history';
    public    $timestamps = false;

    protected $fillable = [
        'paid_asin',
        'sell_price',
        'point_value',
        'actual_price',
        'campaign_text',
        'timestamp',
        'is_unlimited',
    ];

    protected $casts = [
        'sell_price'   => 'integer',
        'point_value'  => 'integer',
        'actual_price' => 'integer',
        'is_unlimited' => 'integer',
    ];

    public function bookMapping()
    {
        return $this->belongsTo(BookMapping::class, 'paid_asin', 'paid_asin');
    }
}
