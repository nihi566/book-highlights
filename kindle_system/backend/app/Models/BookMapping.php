<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class BookMapping extends Model
{
    protected $table      = 'book_mappings';
    protected $primaryKey = 'sample_asin';
    public    $incrementing = false;
    protected $keyType    = 'string';
    public    $timestamps = false;

    protected $fillable = [
        'sample_asin',
        'paid_asin',
        'title',
        'created_at',
        'is_purchased',
        'is_wanted',
    ];

    protected $casts = [
        'is_purchased' => 'integer',
        'is_wanted'    => 'integer',
    ];

    public function priceHistories()
    {
        return $this->hasMany(PriceHistory::class, 'paid_asin', 'paid_asin');
    }
}
